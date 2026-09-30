"""Material atlas generation operator."""

from dataclasses import dataclass

import bpy

from ..utils.atlas_images import compose_atlas, create_packed_image
from ..utils.atlas_layout import initial_atlas_size, pack_materials


@dataclass(frozen=True)
class SourceMaterial:
    material: object
    maps: dict


CATEGORY_SUFFIXES = {
    "Albedo": ("Albedo", "_A"),
    "Metallic": ("Metallic", "_M"),
    "Roughness": ("Roughness", "_R"),
    "Smoothness": ("Smoothness", "_S"),
    "Normal": ("Normal", "_N"),
}


def _linked_image(socket):
    pending = [link.from_node for link in socket.links]
    visited = set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image
        pending.extend(link.from_node for input_socket in node.inputs for link in input_socket.links)
    return None


def _find_map_image(material, principled, category):
    label, suffix = CATEGORY_SUFFIXES[category]
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None and (node.label == label or node.image.name.endswith(suffix)):
            return node.image
    socket_name = "Base Color" if category == "Albedo" else "Metallic" if category == "Metallic" else "Roughness" if category in {"Roughness", "Smoothness"} else "Normal"
    return _linked_image(principled.inputs[socket_name])


def _collect_source_materials(obj, context):
    materials = list(dict.fromkeys(slot.material for slot in obj.material_slots if slot.material))
    if not materials:
        raise ValueError("The active mesh has no material slots.")
    previous_index = obj.active_material_index
    previous_channel_pack = context.scene.atlasmap_channel_pack
    try:
        context.scene.atlasmap_channel_pack = False
        for material in materials:
            slot_index = next(index for index, slot in enumerate(obj.material_slots) if slot.material == material)
            obj.active_material_index = slot_index
            if "FINISHED" not in bpy.ops.atlasmap.convert_shader_to_textures():
                raise ValueError(f"Could not convert material '{material.name}' to textures.")
    finally:
        context.scene.atlasmap_channel_pack = previous_channel_pack
        obj.active_material_index = previous_index

    category = "Smoothness" if context.scene.atlasmap_convert_to_smoothness else "Roughness"
    sources = []
    for material in materials:
        principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        if principled is None:
            raise ValueError(f"Material '{material.name}' has no Principled BSDF node.")
        maps = {name: _find_map_image(material, principled, name) for name in ("Albedo", "Metallic", category, "Normal")}
        missing = [name for name, image in maps.items() if image is None]
        if missing:
            raise ValueError(f"Material '{material.name}' is missing texture categories: {', '.join(missing)}.")
        if len({tuple(image.size) for image in maps.values()}) != 1:
            raise ValueError(f"Material '{material.name}' has texture categories with different sizes.")
        sources.append(SourceMaterial(material, maps))
    return sources, category


def _build_combined_material(name, images, category):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
    locations = {"Albedo": (-700, 180), "Metallic": (-700, -40), category: (-700, -260), "Normal": (-700, -480)}
    for map_category, image in images.items():
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.label = map_category
        texture.location = locations[map_category]
        if map_category == "Albedo":
            links.new(texture.outputs["Color"], principled.inputs["Base Color"])
        elif map_category == "Metallic":
            links.new(texture.outputs["Color"], principled.inputs["Metallic"])
        elif map_category == "Normal":
            normal_map = nodes.new("ShaderNodeNormalMap")
            normal_map.location = (-400, -480)
            links.new(texture.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
        elif map_category == "Smoothness":
            invert = nodes.new("ShaderNodeInvert")
            invert.inputs["Fac"].default_value = 1.0
            invert.location = (-400, -260)
            links.new(texture.outputs["Color"], invert.inputs["Color"])
            links.new(invert.outputs["Color"], principled.inputs["Roughness"])
        else:
            links.new(texture.outputs["Color"], principled.inputs["Roughness"])
    return material


class ATLASMAP_OT_combine_materials(bpy.types.Operator):
    bl_idname = "atlasmap.combine_materials"
    bl_label = "Combine Materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "The active object must be a mesh.")
            return {"CANCELLED"}
        try:
            sources, category = _collect_source_materials(obj, context)
            start_size = initial_atlas_size(sources, ("Albedo", "Metallic", category, "Normal"))
            maximum_size = context.scene.atlasmap_maximum_size
            margin_pixels = max(0, round(context.scene.atlasmap_atlas_margin * start_size))
            atlas_width, atlas_height, placements = pack_materials(sources, start_size, maximum_size, margin_pixels, context.scene.atlasmap_texture_size)
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        background = tuple(context.scene.atlasmap_background_color)
        atlas_images = {}
        for map_category in ("Albedo", "Metallic", category, "Normal"):
            pixels = compose_atlas(sources, placements, atlas_width, atlas_height, map_category, background)
            colorspace = "sRGB" if map_category == "Albedo" else "Non-Color"
            atlas_images[map_category] = create_packed_image(f"{obj.name}_Atlas_{map_category}", pixels, colorspace)

        combined = _build_combined_material(f"{obj.name}_AtlasMaterial", atlas_images, category)
        if len(obj.data.uv_layers) < 2:
            obj.data.uv_layers.new(name="UV1")
        uv_layer = obj.data.uv_layers[1]
        material_by_slot = {index: slot.material for index, slot in enumerate(obj.material_slots)}
        source_by_material = {source.material: source for source in sources}
        for polygon in obj.data.polygons:
            source = source_by_material.get(material_by_slot.get(polygon.material_index))
            if source is None:
                continue
            placement = placements[source]
            for loop_index in polygon.loop_indices:
                uv = uv_layer.data[loop_index].uv
                uv.x = (placement.x + uv.x * placement.width) / atlas_width
                uv.y = (placement.y + uv.y * placement.height) / atlas_height
            polygon.material_index = 0
        obj.data.materials.clear()
        obj.data.materials.append(combined)
        self.report({"INFO"}, f"Combined {len(sources)} materials into {atlas_width}x{atlas_height} atlases.")
        return {"FINISHED"}
