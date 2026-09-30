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


def _existing_mos_node(principled):
    metallic_input = principled.inputs["Metallic"]
    if not metallic_input.links or metallic_input.links[0].from_node.type != "SEPARATE_COLOR":
        return None
    return _direct_texture(metallic_input.links[0].from_node.inputs["Color"])


def _link_ao_sampler(node_tree, image, uv_node, location, ao_socket, sampler=None):
    """Feed the MOS green channel into the AO multiply through a second sampler, so AO reads UV1 while M/R/S use the default UV map."""
    links = node_tree.links
    if sampler is None:
        sampler = node_tree.nodes.new("ShaderNodeTexImage")
        sampler.image = image
        sampler.label = "MOS (AO)"
        sampler.location = location
    if uv_node is not None:
        links.new(uv_node.outputs["UV"], sampler.inputs["Vector"])
    separate = next((link.to_node for link in sampler.outputs["Color"].links if link.to_node.type == "SEPARATE_COLOR"), None)
    if separate is None:
        separate = node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.mode = "RGB"
        separate.label = "MOS AO Channel"
        separate.location = (sampler.location.x + 280, sampler.location.y)
        links.new(sampler.outputs["Color"], separate.inputs["Color"])
    links.new(separate.outputs["Green"], ao_socket)


def _add_ao_to_mos(node_tree, mos_node, ao_node, ao_socket):
    """Write a newly baked AO texture into the green channel of an existing MOS image and rewire the AO sampler."""
    image = mos_node.image
    width = max(image.size[0], ao_node.image.size[0])
    height = max(image.size[1], ao_node.image.size[1])
    pixels = image_to_array(image)
    if tuple(image.size) != (width, height):
        method = resolve_resample_method("AUTO", max(width / image.size[0], height / image.size[1]))
        pixels = resample_pixels(pixels, width, height, method)
        image.scale(width, height)
    pixels[..., 1] = _channel(ao_node.image, width, height)
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
    image.pack()

    uv_node = ao_node.inputs["Vector"].links[0].from_node if ao_node.inputs["Vector"].links else None
    location = ao_node.location.copy()
    sampler = next((node for node in node_tree.nodes if node.type == "TEX_IMAGE" and node.image == image and node != mos_node), None)
    node_tree.nodes.remove(ao_node)
    _link_ao_sampler(node_tree, image, uv_node, location, ao_socket, sampler)
    return width, height


class ATLASMAP_OT_pack_mos(bpy.types.Operator):
    bl_idname = "atlasmap.pack_mos"
    bl_label = "Pack MOS"
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

        sources = _detect_mos_sources(principled)
        existing_mos = _existing_mos_node(principled)
        if existing_mos is not None:
            if sources["ao"] is None:
                self.report({"INFO"}, "MOS already packed; no new AO to add.")
                return {"CANCELLED"}
            empty = [image.name for image in (existing_mos.image, sources["ao"].image) if 0 in image.size[:]]
            if empty:
                self.report({"ERROR"}, f"Image(s) have no pixel data: {', '.join(empty)}.")
                return {"CANCELLED"}
            width, height = _add_ao_to_mos(node_tree, existing_mos, sources["ao"], sources["ao_socket"])
            arrange_material_nodes(material)
            self.report({"INFO"}, f"Added AO to {existing_mos.image.name} at {width}x{height}.")
            return {"FINISHED"}
        if sum(sources[key] is not None for key in ("metallic", "rs", "ao")) < 2:
            self.report({"INFO"}, "Nothing to pack; at least two of Metallic, Roughness/Smoothness and AO textures are needed.")
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
                self.report({"ERROR"}, "Could not generate the missing Metallic or Roughness/Smoothness texture.")
                return {"CANCELLED"}

        texture_nodes = [sources[key] for key in ("metallic", "rs", "ao") if sources[key] is not None]
        empty = [node.image.name for node in texture_nodes if 0 in node.image.size[:]]
        if empty:
            self.report({"ERROR"}, f"Image(s) have no pixel data: {', '.join(empty)}.")
            return {"CANCELLED"}
        width = max(node.image.size[0] for node in texture_nodes)
        height = max(node.image.size[1] for node in texture_nodes)
        pixels = np.ones((height, width, 4), dtype=np.float32)
        pixels[..., 0] = _channel(sources["metallic"].image, width, height)
        pixels[..., 2] = _channel(sources["rs"].image, width, height)
        if sources["ao"] is not None:
            pixels[..., 1] = _channel(sources["ao"].image, width, height)
        image = create_packed_image(f"{material.name}_MOS", pixels, "Non-Color")

        links = node_tree.links
        metallic_location = sources["metallic"].location.copy()
        ao_node = sources["ao"]
        ao_location = ao_node.location.copy() if ao_node else None
        uv_node = ao_node.inputs["Vector"].links[0].from_node if ao_node and ao_node.inputs["Vector"].links else None
        for node in texture_nodes:
            node_tree.nodes.remove(node)

        mos_node = node_tree.nodes.new("ShaderNodeTexImage")
        mos_node.image = image
        mos_node.label = "MOS"
        mos_node.location = metallic_location
        separate = node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.mode = "RGB"
        separate.label = "MOS Channels"
        separate.location = (metallic_location.x + 280, metallic_location.y)
        links.new(mos_node.outputs["Color"], separate.inputs["Color"])
        links.new(separate.outputs["Red"], principled.inputs["Metallic"])
        links.new(separate.outputs["Blue"], sources["invert"].inputs["Color"] if sources["invert"] else principled.inputs["Roughness"])

        if ao_node is not None:
            _link_ao_sampler(node_tree, image, uv_node, ao_location, sources["ao_socket"])

        arrange_material_nodes(material)
        blue_name = "Smoothness" if sources["invert"] else "Roughness"
        ao_text = "AO" if ao_node is not None else "AO defaulted to 1.0"
        self.report({"INFO"}, f"Packed {image.name} at {width}x{height}: Metallic, {ao_text}, {blue_name}.")
        return {"FINISHED"}


class ATLASMAP_OT_unpack_mos(bpy.types.Operator):
    bl_idname = "atlasmap.unpack_mos"
    bl_label = "Unpack MOS"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self.report({"INFO"}, "Unpack MOS is not implemented yet.")
        return {"CANCELLED"}


class ATLASMAP_OT_switch_smoothness_roughness(bpy.types.Operator):
    bl_idname = "atlasmap.switch_smoothness_roughness"
    bl_label = "Convert Smoothness/Roughness"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self.report({"INFO"}, "Convert Smoothness/Roughness is not implemented yet.")
        return {"CANCELLED"}
