"""Texture channel conversion operators."""

import bpy
import numpy as np

from ..utils.atlas_images import create_packed_image, image_to_array
from ..utils.node_layout import arrange_material_nodes
from ..utils.resampling import resample_pixels, resolve_resample_method


def _direct_texture(socket):
    if not socket.links:
        return None
    node = socket.links[0].from_node
    return node if node.type == "TEX_IMAGE" and node.image is not None else None


def _detect_mos_sources(principled):
    """Find the separate Metallic, Roughness/Smoothness and AO texture nodes feeding the Principled BSDF."""
    sources = {"metallic": _direct_texture(principled.inputs["Metallic"]), "rs": None, "invert": None, "ao": None, "ao_socket": None}
    roughness_input = principled.inputs["Roughness"]
    sources["rs"] = _direct_texture(roughness_input)
    if sources["rs"] is None and roughness_input.links and roughness_input.links[0].from_node.type == "INVERT":
        invert = roughness_input.links[0].from_node
        sources["rs"] = _direct_texture(invert.inputs["Color"])
        if sources["rs"] is not None:
            sources["invert"] = invert
    base_color_input = principled.inputs["Base Color"]
    if base_color_input.links:
        mix = base_color_input.links[0].from_node
        if mix.type == "MIX" and mix.data_type == "RGBA" and mix.blend_type == "MULTIPLY":
            ao_socket = next((socket for socket in mix.inputs if socket.name == "B" and socket.is_linked), None)
            if ao_socket is not None:
                sources["ao"] = _direct_texture(ao_socket)
                sources["ao_socket"] = ao_socket if sources["ao"] is not None else None
    return sources


def _channel(image, width, height):
    pixels = image_to_array(image)
    source_width, source_height = image.size[:]
    if (source_width, source_height) != (width, height):
        method = resolve_resample_method("AUTO", max(width / source_width, height / source_height))
        pixels = resample_pixels(pixels, width, height, method)
    return pixels[..., 0]


MOS_LAYOUT = {"name": "MOS", "metallic": 0, "ao": 1, "rough": 2}
ORM_LAYOUT = {"name": "ORM", "ao": 0, "rough": 1, "metallic": 2}
_OUTPUTS = ("Red", "Green", "Blue")


def _existing_packed_node(principled, layout):
    metallic_input = principled.inputs["Metallic"]
    if not metallic_input.links:
        return None
    link = metallic_input.links[0]
    if link.from_node.type != "SEPARATE_COLOR" or link.from_socket.name != _OUTPUTS[layout["metallic"]]:
        return None
    return _direct_texture(link.from_node.inputs["Color"])


def _existing_mos_node(principled):
    return _existing_packed_node(principled, MOS_LAYOUT)


def _existing_orm_node(principled):
    return _existing_packed_node(principled, ORM_LAYOUT)


def _link_ao_sampler(node_tree, image, uv_node, location, ao_socket, layout, sampler=None):
    """Feed the packed AO channel into the AO multiply through a second sampler, so AO reads UV1 while the other channels use the default UV map."""
    links = node_tree.links
    if sampler is None:
        sampler = node_tree.nodes.new("ShaderNodeTexImage")
        sampler.image = image
        sampler.label = f"{layout['name']} (AO)"
        sampler.location = location
    if uv_node is not None:
        links.new(uv_node.outputs["UV"], sampler.inputs["Vector"])
    separate = next((link.to_node for link in sampler.outputs["Color"].links if link.to_node.type == "SEPARATE_COLOR"), None)
    if separate is None:
        separate = node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.mode = "RGB"
        separate.label = f"{layout['name']} AO Channel"
        separate.location = (sampler.location.x + 280, sampler.location.y)
        links.new(sampler.outputs["Color"], separate.inputs["Color"])
    links.new(separate.outputs[_OUTPUTS[layout["ao"]]], ao_socket)


def _add_ao_to_packed(node_tree, packed_node, ao_node, ao_socket, layout):
    """Write a newly baked AO texture into the AO channel of an existing packed image and rewire the AO sampler."""
    image = packed_node.image
    width = max(image.size[0], ao_node.image.size[0])
    height = max(image.size[1], ao_node.image.size[1])
    pixels = image_to_array(image)
    if tuple(image.size) != (width, height):
        method = resolve_resample_method("AUTO", max(width / image.size[0], height / image.size[1]))
        pixels = resample_pixels(pixels, width, height, method)
        image.scale(width, height)
    pixels[..., layout["ao"]] = _channel(ao_node.image, width, height)
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
    image.pack()

    uv_node = ao_node.inputs["Vector"].links[0].from_node if ao_node.inputs["Vector"].links else None
    location = ao_node.location.copy()
    sampler = next((node for node in node_tree.nodes if node.type == "TEX_IMAGE" and node.image == image and node != packed_node), None)
    node_tree.nodes.remove(ao_node)
    _link_ao_sampler(node_tree, image, uv_node, location, ao_socket, layout, sampler)
    return width, height


def _pack(operator, context, layout):
    name = layout["name"]
    obj = context.active_object
    material = obj.active_material if obj else None
    if material is None or not material.use_nodes:
        operator.report({"ERROR"}, "The active object needs a material with nodes enabled.")
        return {"CANCELLED"}
    node_tree = material.node_tree
    principled = next((node for node in node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        operator.report({"ERROR"}, "No Principled BSDF node was found in the active material.")
        return {"CANCELLED"}

    other_layout, other_unpack = (ORM_LAYOUT, bpy.ops.atlasmap.unpack_orm) if layout is MOS_LAYOUT else (MOS_LAYOUT, bpy.ops.atlasmap.unpack_mos)
    if _existing_packed_node(principled, other_layout) is not None and "FINISHED" not in other_unpack():
        operator.report({"ERROR"}, f"Could not unpack the {other_layout['name']} texture before packing {name}.")
        return {"CANCELLED"}

    sources = _detect_mos_sources(principled)
    existing = _existing_packed_node(principled, layout)
    if existing is not None:
        if sources["ao"] is None:
            operator.report({"INFO"}, f"{name} already packed; no new AO to add.")
            return {"CANCELLED"}
        empty = [image.name for image in (existing.image, sources["ao"].image) if 0 in image.size[:]]
        if empty:
            operator.report({"ERROR"}, f"Image(s) have no pixel data: {', '.join(empty)}.")
            return {"CANCELLED"}
        width, height = _add_ao_to_packed(node_tree, existing, sources["ao"], sources["ao_socket"], layout)
        arrange_material_nodes(material)
        operator.report({"INFO"}, f"Added AO to {existing.image.name} at {width}x{height}.")
        return {"FINISHED"}
    if sum(sources[key] is not None for key in ("metallic", "rs", "ao")) < 2:
        operator.report({"INFO"}, "Nothing to pack; at least two of Metallic, Roughness/Smoothness and AO textures are needed.")
        return {"CANCELLED"}
    if sources["metallic"] is None or sources["rs"] is None:
        scene = context.scene
        previous_channel_pack = scene.atlasmap_channel_pack
        try:
            scene.atlasmap_channel_pack = False
            result = bpy.ops.atlasmap.convert_shader_to_textures()
        finally:
            scene.atlasmap_channel_pack = previous_channel_pack
        sources = _detect_mos_sources(principled)
        if "FINISHED" not in result or sources["metallic"] is None or sources["rs"] is None:
            operator.report({"ERROR"}, "Could not generate the missing Metallic or Roughness/Smoothness texture.")
            return {"CANCELLED"}

    texture_nodes = [sources[key] for key in ("metallic", "rs", "ao") if sources[key] is not None]
    empty = [node.image.name for node in texture_nodes if 0 in node.image.size[:]]
    if empty:
        operator.report({"ERROR"}, f"Image(s) have no pixel data: {', '.join(empty)}.")
        return {"CANCELLED"}
    # ORM always stores roughness, so a smoothness source is inverted and its Invert node dropped.
    to_roughness = layout is ORM_LAYOUT and sources["invert"] is not None
    width = max(node.image.size[0] for node in texture_nodes)
    height = max(node.image.size[1] for node in texture_nodes)
    pixels = np.ones((height, width, 4), dtype=np.float32)
    pixels[..., layout["metallic"]] = _channel(sources["metallic"].image, width, height)
    rough = _channel(sources["rs"].image, width, height)
    pixels[..., layout["rough"]] = 1.0 - rough if to_roughness else rough
    if sources["ao"] is not None:
        pixels[..., layout["ao"]] = _channel(sources["ao"].image, width, height)
    image = create_packed_image(f"{material.name}_{name}", pixels, "Non-Color")

    links = node_tree.links
    metallic_location = sources["metallic"].location.copy()
    ao_node = sources["ao"]
    ao_location = ao_node.location.copy() if ao_node else None
    uv_node = ao_node.inputs["Vector"].links[0].from_node if ao_node and ao_node.inputs["Vector"].links else None
    for node in texture_nodes:
        node_tree.nodes.remove(node)
    invert = sources["invert"]
    if to_roughness:
        node_tree.nodes.remove(invert)
        invert = None

    packed_node = node_tree.nodes.new("ShaderNodeTexImage")
    packed_node.image = image
    packed_node.label = name
    packed_node.location = metallic_location
    separate = node_tree.nodes.new("ShaderNodeSeparateColor")
    separate.mode = "RGB"
    separate.label = f"{name} Channels"
    separate.location = (metallic_location.x + 280, metallic_location.y)
    links.new(packed_node.outputs["Color"], separate.inputs["Color"])
    links.new(separate.outputs[_OUTPUTS[layout["metallic"]]], principled.inputs["Metallic"])
    links.new(separate.outputs[_OUTPUTS[layout["rough"]]], invert.inputs["Color"] if invert else principled.inputs["Roughness"])

    if ao_node is not None:
        _link_ao_sampler(node_tree, image, uv_node, ao_location, sources["ao_socket"], layout)

    arrange_material_nodes(material)
    channel_names = {"metallic": "Metallic", "rough": "Smoothness" if invert else "Roughness", "ao": "AO" if ao_node is not None else "AO defaulted to 1.0"}
    channels_text = ", ".join(channel_names[key] for key in sorted(channel_names, key=lambda key: layout[key]))
    operator.report({"INFO"}, f"Packed {image.name} at {width}x{height}: {channels_text}.")
    return {"FINISHED"}


class ATLASMAP_OT_pack_mos(bpy.types.Operator):
    bl_idname = "atlasmap.pack_mos"
    bl_label = "Pack MOS"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _pack(self, context, MOS_LAYOUT)


class ATLASMAP_OT_pack_orm(bpy.types.Operator):
    bl_idname = "atlasmap.pack_orm"
    bl_label = "Pack ORM"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _pack(self, context, ORM_LAYOUT)


def _ao_mix_socket(node_tree, principled):
    """Return the Multiply Mix node's B socket feeding Base Color, inserting that Mix node if it does not exist."""
    base_color_input = principled.inputs["Base Color"]
    source = base_color_input.links[0].from_node if base_color_input.links else None
    if source is not None and source.type == "MIX" and source.data_type == "RGBA" and source.blend_type == "MULTIPLY":
        mix = source
    else:
        mix = node_tree.nodes.new("ShaderNodeMix")
        mix["atlasmap_ao_multiply"] = True
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        a_input = next(socket for socket in mix.inputs if socket.identifier == "A_Color")
        if base_color_input.links:
            node_tree.links.new(base_color_input.links[0].from_socket, a_input)
        else:
            a_input.default_value = base_color_input.default_value[:]
        node_tree.links.new(next(socket for socket in mix.outputs if socket.identifier == "Result_Color"), base_color_input)
    return next(socket for socket in mix.inputs if socket.identifier == "B_Color")


def _channel_image(name, pixels, index):
    channel = np.ones_like(pixels)
    channel[..., :3] = pixels[..., index:index + 1]
    return create_packed_image(name, channel, "Non-Color")


def _texture_node(node_tree, image, label, location):
    node = node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    node.label = label
    node.location = location
    return node


def _unpack(operator, context, layout):
    name = layout["name"]
    obj = context.active_object
    material = obj.active_material if obj else None
    if material is None or not material.use_nodes:
        operator.report({"ERROR"}, "The active object needs a material with nodes enabled.")
        return {"CANCELLED"}
    node_tree = material.node_tree
    principled = next((node for node in node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        operator.report({"ERROR"}, "No Principled BSDF node was found in the active material.")
        return {"CANCELLED"}
    packed_node = _existing_packed_node(principled, layout)
    if packed_node is None:
        operator.report({"INFO"}, f"No {name} texture to unpack.")
        return {"CANCELLED"}
    packed_image = packed_node.image
    if 0 in packed_image.size[:]:
        operator.report({"ERROR"}, f"Image has no pixel data: {packed_image.name}.")
        return {"CANCELLED"}

    separate = principled.inputs["Metallic"].links[0].from_node
    roughness_input = principled.inputs["Roughness"]
    invert = roughness_input.links[0].from_node if roughness_input.links else None
    if invert is None or invert.type != "INVERT" or not any(link.from_node == separate for link in invert.inputs["Color"].links):
        invert = None
    ao_sampler = next((node for node in node_tree.nodes if node.type == "TEX_IMAGE" and node.image == packed_image and node != packed_node), None)
    ao_separate = next((link.to_node for link in ao_sampler.outputs["Color"].links if link.to_node.type == "SEPARATE_COLOR"), None) if ao_sampler else None
    ao_socket = next((link.to_socket for link in ao_separate.outputs[_OUTPUTS[layout["ao"]]].links), None) if ao_separate else None
    uv_node = ao_sampler.inputs["Vector"].links[0].from_node if ao_sampler and ao_sampler.inputs["Vector"].links else None
    if ao_socket is None:
        ao_socket = _ao_mix_socket(node_tree, principled)
        uv_node = None

    pixels = image_to_array(packed_image)
    rough_name, rough_suffix = ("Smoothness", "_S") if invert else ("Roughness", "_R")
    metallic_image = _channel_image(f"{material.name}_M", pixels, layout["metallic"])
    ao_image = _channel_image(f"{material.name}_AO", pixels, layout["ao"])
    rough_image = _channel_image(f"{material.name}{rough_suffix}", pixels, layout["rough"])

    links = node_tree.links
    location = packed_node.location.copy()
    metallic_node = _texture_node(node_tree, metallic_image, "Metallic", location)
    rough_node = _texture_node(node_tree, rough_image, rough_name, location)
    ao_node = _texture_node(node_tree, ao_image, "Ambient Occlusion", ao_sampler.location.copy() if ao_sampler else location)
    links.new(metallic_node.outputs["Color"], principled.inputs["Metallic"])
    links.new(rough_node.outputs["Color"], invert.inputs["Color"] if invert else roughness_input)
    links.new(ao_node.outputs["Color"], ao_socket)
    if uv_node is not None:
        links.new(uv_node.outputs["UV"], ao_node.inputs["Vector"])
    for node in (packed_node, separate, ao_sampler, ao_separate):
        if node is not None:
            node_tree.nodes.remove(node)

    arrange_material_nodes(material)
    operator.report({"INFO"}, f"Unpacked {packed_image.name} into {metallic_image.name}, {ao_image.name}, {rough_image.name}.")
    return {"FINISHED"}


class ATLASMAP_OT_unpack_mos(bpy.types.Operator):
    bl_idname = "atlasmap.unpack_mos"
    bl_label = "Unpack MOS"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _unpack(self, context, MOS_LAYOUT)


class ATLASMAP_OT_unpack_orm(bpy.types.Operator):
    bl_idname = "atlasmap.unpack_orm"
    bl_label = "Unpack ORM"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _unpack(self, context, ORM_LAYOUT)


class ATLASMAP_OT_switch_smoothness_roughness(bpy.types.Operator):
    bl_idname = "atlasmap.switch_smoothness_roughness"
    bl_label = "Convert S/R"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj else None
        if material is None or not material.use_nodes:
            self.report({"ERROR"}, "The active object needs a material with nodes enabled.")
            return {"CANCELLED"}
        node_tree = material.node_tree
        principled = next((node for node in node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        if principled is None:
            self.report({"ERROR"}, "No Principled BSDF node was found in the active material.")
            return {"CANCELLED"}

        # Smoothness is TEX_IMAGE/MOS blue -> Invert -> Roughness; roughness is linked directly.
        roughness_input = principled.inputs["Roughness"]
        link = roughness_input.links[0] if roughness_input.links else None
        invert = link.from_node if link and link.from_node.type == "INVERT" else None
        if invert is not None:
            source_socket = invert.inputs["Color"].links[0].from_socket if invert.inputs["Color"].links else None
        else:
            source_socket = link.from_socket if link else None
        source = source_socket.node if source_socket else None
        mos_node = _direct_texture(source.inputs["Color"]) if source and source.type == "SEPARATE_COLOR" and source_socket.name == "Blue" else None
        if source is not None and source.type == "TEX_IMAGE" and source.image is not None:
            image, channels = source.image, slice(0, 3)
        elif mos_node is not None:
            image, channels = mos_node.image, slice(2, 3)
        else:
            self.report({"INFO"}, "No Roughness/Smoothness texture found.")
            return {"CANCELLED"}
        if 0 in image.size[:]:
            self.report({"ERROR"}, f"Image has no pixel data: {image.name}.")
            return {"CANCELLED"}

        pixels = image_to_array(image)
        pixels[..., channels] = 1.0 - pixels[..., channels]
        image.pixels.foreach_set(pixels.ravel())
        image.pack()

        to_smoothness = invert is None
        from_name, to_name = ("Roughness", "Smoothness") if to_smoothness else ("Smoothness", "Roughness")
        if mos_node is None:
            if image.name.endswith(f"_{from_name[0]}"):
                image.name = f"{image.name[:-2]}_{to_name[0]}"
            source.label = to_name

        links = node_tree.links
        if to_smoothness:
            invert = node_tree.nodes.new("ShaderNodeInvert")
            invert.label = "Smoothness Invert"
            invert.inputs["Fac"].default_value = 1.0
            links.new(source_socket, invert.inputs["Color"])
            links.new(invert.outputs["Color"], roughness_input)
        else:
            node_tree.nodes.remove(invert)
            links.new(source_socket, roughness_input)

        arrange_material_nodes(material)
        self.report({"INFO"}, f"Converted {from_name} to {to_name} in {image.name}.")
        return {"FINISHED"}
