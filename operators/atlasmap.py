"""Material atlas generation operator."""

import math
from dataclasses import dataclass

import bpy

from ..utils.atlas_images import compose_atlas, create_packed_image, image_to_array
from ..utils.atlas_layout import initial_atlas_size, pack_materials
from ..utils.node_layout import arrange_material_nodes
from ..utils.resampling import resample_pixels, resolve_resample_method
from .texture_channels import _existing_mos_node


@dataclass(eq=False)
class SourceMaterial:
    material: object
    maps: dict
    width: int
    height: int


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


def _is_smoothness(principled):
    links = principled.inputs["Roughness"].links
    return bool(links) and links[0].from_node.type == "INVERT"


def _run(operator, material, action):
    if "FINISHED" not in operator():
        raise ValueError(f"Could not {action} for material '{material.name}'.")


def _normalize_materials(obj, context, materials):
    """Unpack MOS, convert every material to textures, and match each material's R/S type to the Smoothness toggle."""
    scene = context.scene
    previous_index = obj.active_material_index
    previous_channel_pack = scene.atlasmap_channel_pack
    try:
        scene.atlasmap_channel_pack = False
        for material in materials:
            obj.active_material_index = next(index for index, slot in enumerate(obj.material_slots) if slot.material == material)
            principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None) if material.use_nodes else None
            if principled is None:
                raise ValueError(f"Material '{material.name}' has no Principled BSDF node.")
            if _existing_mos_node(principled) is not None:
                _run(bpy.ops.atlasmap.unpack_mos, material, "unpack the MOS texture")
            _run(bpy.ops.atlasmap.convert_shader_to_textures, material, "convert the shader to textures")
            if _is_smoothness(principled) != scene.atlasmap_convert_to_smoothness:
                _run(bpy.ops.atlasmap.switch_smoothness_roughness, material, "convert Smoothness/Roughness")
    finally:
        scene.atlasmap_channel_pack = previous_channel_pack
        obj.active_material_index = previous_index


def _estimate_tile_size(material, texture_size):
    """Predict a material's atlas tile size from image sizes only; maps still to be generated use texture_size."""
    principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None) if material.use_nodes else None
    if principled is None:
        return None
    sizes = []
    for category in ("Albedo", "Metallic", "Roughness", "Normal"):
        image = _find_map_image(material, principled, category)
        sizes.append(tuple(image.size) if image is not None else (texture_size, texture_size))
    return max(width for width, _ in sizes), max(height for _, height in sizes)


def _check_atlas_can_fit(materials, scene):
    """Fail fast, before any texture work, when the tiles cannot fit the maximum atlas size even with perfect packing."""
    maximum = scene.atlasmap_maximum_size
    total_area = 0
    for material in materials:
        size = _estimate_tile_size(material, scene.atlasmap_texture_size)
        if size is None:
            continue
        width, height = size
        if width > maximum or height > maximum:
            raise ValueError(f"Material '{material.name}' needs a {width}x{height} tile, larger than the maximum atlas size {maximum}.")
        total_area += width * height
    if total_area > maximum * maximum:
        side = math.ceil(math.sqrt(total_area))
        raise ValueError(f"Textures need at least {total_area} pixels (about {side}x{side}) but the maximum atlas is {maximum}x{maximum}.")


def _collect_source_materials(obj, context):
    materials = list(dict.fromkeys(slot.material for slot in obj.material_slots if slot.material))
    if not materials:
        raise ValueError("The active mesh has no material slots.")
    _check_atlas_can_fit(materials, context.scene)
    _normalize_materials(obj, context, materials)

    category = "Smoothness" if context.scene.atlasmap_convert_to_smoothness else "Roughness"
    sources = []
    for material in materials:
        principled = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
        maps = {name: _find_map_image(material, principled, name) for name in ("Albedo", "Metallic", category, "Normal")}
        missing = [name for name, image in maps.items() if image is None]
        if missing:
            raise ValueError(f"Material '{material.name}' is missing texture categories: {', '.join(missing)}.")
        empty = [image.name for image in maps.values() if 0 in image.size[:]]
        if empty:
            raise ValueError(f"Image(s) have no pixel data: {', '.join(empty)}.")
        # Textures of one material share a size; smaller ones are resized to the largest.
        width = max(image.size[0] for image in maps.values())
        height = max(image.size[1] for image in maps.values())
        arrays = {}
        for name, image in maps.items():
            pixels = image_to_array(image)
            if tuple(image.size) != (width, height):
                method = resolve_resample_method("AUTO", max(width / image.size[0], height / image.size[1]))
                pixels = resample_pixels(pixels, width, height, method)
            arrays[name] = pixels
        sources.append(SourceMaterial(material, arrays, width, height))
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
    arrange_material_nodes(material)
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
        uv_layers = obj.data.uv_layers
        if not uv_layers:
            self.report({"ERROR"}, "The active mesh needs a UV map.")
            return {"CANCELLED"}
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        scene = context.scene
        margin = scene.atlasmap_atlas_margin
        try:
            sources, category = _collect_source_materials(obj, context)
            start_size = initial_atlas_size(sources)
            atlas_width, atlas_height, placements = pack_materials(sources, start_size, scene.atlasmap_maximum_size, margin)
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        # Packing succeeded, so pixels can be written now.
        background = tuple(scene.atlasmap_background_color)
        atlas_images = {}
        for map_category in ("Albedo", "Metallic", category, "Normal"):
            pixels = compose_atlas(sources, placements, atlas_width, atlas_height, map_category, background, margin)
            colorspace = "sRGB" if map_category == "Albedo" else "Non-Color"
            atlas_images[map_category] = create_packed_image(f"{obj.name}_Atlas_{map_category}", pixels, colorspace)
        combined = _build_combined_material(f"{obj.name}_AtlasMaterial", atlas_images, category)

        # Remap the UV map the textures sample (the render UV map); UV1 used for AO is left alone.
        uv_layer = next((layer for layer in uv_layers if layer.active_render), uv_layers[0])
        material_by_slot = {index: slot.material for index, slot in enumerate(obj.material_slots)}
        source_by_material = {source.material: source for source in sources}
        tiled = False
        for polygon in obj.data.polygons:
            source = source_by_material.get(material_by_slot.get(polygon.material_index))
            if source is not None:
                placement = placements[source]
                for loop_index in polygon.loop_indices:
                    uv = uv_layer.data[loop_index].uv
                    tiled = tiled or not (-1e-4 <= uv.x <= 1.0001 and -1e-4 <= uv.y <= 1.0001)
                    uv.x = (placement.x + uv.x * placement.width) / atlas_width
                    uv.y = (atlas_height - placement.y - placement.height + uv.y * placement.height) / atlas_height
            polygon.material_index = 0
        obj.data.materials.clear()
        obj.data.materials.append(combined)

        message = f"Combined {len(sources)} materials into {atlas_width}x{atlas_height} atlases."
        if tiled:
            self.report({"WARNING"}, message + f" Some UVs in '{uv_layer.name}' were outside 0-1 and will sample neighbouring atlas regions.")
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}
