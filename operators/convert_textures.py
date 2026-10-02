"""Operators for generating solid-color material textures."""

import bpy
import numpy as np

from ..utils.material_graph import create_albedo_texture
from ..utils.node_layout import arrange_material_nodes

# Fraction of the 0-1 tile the material's UVs are fitted into, keeping them away from the tile edges to avoid atlas bleed.
UV_TILE_FILL = 0.7


def _fit_uvs_to_tile_center(obj, material):
    """Uniformly scale the UVs of faces using material so their bounds fill the centre UV_TILE_FILL of the tile."""
    if obj.type != "MESH" or not obj.data.uv_layers:
        return False
    previous_mode = obj.mode
    if previous_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        mesh = obj.data
        uv_layer = next((layer for layer in mesh.uv_layers if layer.active_render), mesh.uv_layers[0])
        slots = {index for index, slot in enumerate(obj.material_slots) if slot.material == material}
        loops = np.array([loop for polygon in mesh.polygons if polygon.material_index in slots for loop in polygon.loop_indices], dtype=np.int64)
        if not len(loops):
            return False
        uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        uv_layer.data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)
        selected = uvs[loops]
        low, high = selected.min(axis=0), selected.max(axis=0)
        extent = (high - low).max()
        scale = UV_TILE_FILL / extent if extent > 0 else 0.0
        uvs[loops] = (selected - (low + high) / 2) * scale + 0.5
        uv_layer.data.foreach_set("uv", uvs.ravel())
        mesh.update()
        return True
    finally:
        if previous_mode != "OBJECT":
            bpy.ops.object.mode_set(mode=previous_mode)


class ATLASMAP_OT_convert_shader_to_textures(bpy.types.Operator):
    bl_idname = "atlasmap.convert_shader_to_textures"
    bl_label = "Shader to Textures"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj else None
        if material is None or not material.use_nodes:
            self.report({"ERROR"}, "The active object needs a material with nodes enabled.")
            return {"CANCELLED"}
        principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        if principled is None:
            self.report({"ERROR"}, "No Principled BSDF node was found in the active material.")
            return {"CANCELLED"}

        smoothness_enabled = context.scene.atlasmap_convert_to_smoothness
        channel_pack_enabled = context.scene.atlasmap_channel_pack
        values = [("Albedo", "_A", "Base Color", principled.inputs["Base Color"].default_value[:])]
        if not channel_pack_enabled:
            values.extend((("Metallic", "_M", "Metallic", principled.inputs["Metallic"].default_value), ("Smoothness" if smoothness_enabled else "Roughness", "_S" if smoothness_enabled else "_R", "Roughness", principled.inputs["Roughness"].default_value)))
        texture_size = context.scene.atlasmap_texture_size
        node_tree = material.node_tree
        created_maps = []
        skipped_maps = []
        for index, (map_name, suffix, socket_name, value) in enumerate(values):
            input_socket = principled.inputs[socket_name]
            if input_socket.is_linked:
                skipped_maps.append(map_name)
                continue
            if socket_name == "Roughness" and smoothness_enabled:
                value = 1.0 - value
            if socket_name == "Base Color":
                texture_node = create_albedo_texture(material, principled, texture_size)
                node_tree.links.new(texture_node.outputs["Color"], input_socket)
                created_maps.append(map_name)
                continue
            color = (value, value, value, 1.0)
            image = bpy.data.images.new(f"{material.name}{suffix}", width=texture_size, height=texture_size, alpha=True, float_buffer=True)
            image.colorspace_settings.name = "Non-Color"
            image.pixels.foreach_set(color * (texture_size * texture_size))
            image.pack()
            texture_node = node_tree.nodes.new("ShaderNodeTexImage")
            texture_node.image = image
            texture_node.label = map_name
            texture_node.location = (principled.location.x - 560, principled.location.y - index * 360)
            if socket_name == "Roughness" and smoothness_enabled:
                invert_node = node_tree.nodes.new("ShaderNodeInvert")
                invert_node.label = "Smoothness Invert"
                invert_node.inputs["Fac"].default_value = 1.0
                invert_node.location = (principled.location.x - 280, principled.location.y - index * 360)
                node_tree.links.new(texture_node.outputs["Color"], invert_node.inputs["Color"])
                node_tree.links.new(invert_node.outputs["Color"], input_socket)
            else:
                node_tree.links.new(texture_node.outputs["Color"], input_socket)
            created_maps.append(map_name)

        mos_created = False
        if channel_pack_enabled:
            metallic_input = principled.inputs["Metallic"]
            roughness_input = principled.inputs["Roughness"]
            if metallic_input.is_linked:
                skipped_maps.append("Metallic")
            if roughness_input.is_linked:
                skipped_maps.append("Smoothness" if smoothness_enabled else "Roughness")
            if not metallic_input.is_linked or not roughness_input.is_linked:
                metallic_value = metallic_input.default_value
                packed_blue = 1.0 - roughness_input.default_value if smoothness_enabled else roughness_input.default_value
                image = bpy.data.images.new(f"{material.name}_MOS", width=texture_size, height=texture_size, alpha=True, float_buffer=True)
                image.colorspace_settings.name = "Non-Color"
                image.pixels.foreach_set((metallic_value, 0.0, packed_blue, 1.0) * (texture_size * texture_size))
                image.pack()
                texture_node = node_tree.nodes.new("ShaderNodeTexImage")
                texture_node.image = image
                texture_node.label = "Metallic / Smoothness" if smoothness_enabled else "Metallic / Roughness"
                texture_node.location = (principled.location.x - 560, principled.location.y - len(values) * 360)
                separate = node_tree.nodes.new("ShaderNodeSeparateColor")
                separate.mode = "RGB"
                separate.label = "MOS Channels"
                separate.location = (principled.location.x - 280, texture_node.location.y)
                node_tree.links.new(texture_node.outputs["Color"], separate.inputs["Color"])
                if not metallic_input.is_linked:
                    node_tree.links.new(separate.outputs["Red"], metallic_input)
                if smoothness_enabled:
                    invert_node = node_tree.nodes.new("ShaderNodeInvert")
                    invert_node.label = "Smoothness Invert"
                    invert_node.inputs["Fac"].default_value = 1.0
                    invert_node.location = (principled.location.x - 280, texture_node.location.y - 360)
                    node_tree.links.new(separate.outputs["Blue"], invert_node.inputs["Color"])
                    if not roughness_input.is_linked:
                        node_tree.links.new(invert_node.outputs["Color"], roughness_input)
                elif not roughness_input.is_linked:
                    node_tree.links.new(separate.outputs["Blue"], roughness_input)
                created_maps.append("MOS")
                mos_created = True

        normal_input = principled.inputs["Normal"]
        if normal_input.is_linked:
            skipped_maps.append("Normal")
        else:
            normal_row = len(values) + (2 if mos_created and smoothness_enabled else int(mos_created))
            image = bpy.data.images.new(f"{material.name}_N", width=texture_size, height=texture_size, alpha=True, float_buffer=True)
            image.colorspace_settings.name = "Non-Color"
            image.pixels.foreach_set((0.5, 0.5, 1.0, 1.0) * (texture_size * texture_size))
            image.pack()
            texture_node = node_tree.nodes.new("ShaderNodeTexImage")
            texture_node.image = image
            texture_node.label = "Normal"
            texture_node.location = (principled.location.x - 560, principled.location.y - normal_row * 360)
            normal_map = node_tree.nodes.new("ShaderNodeNormalMap")
            normal_map.label = "Normal Map"
            normal_map.space = "TANGENT"
            normal_map.location = (principled.location.x - 280, principled.location.y - normal_row * 360)
            node_tree.links.new(texture_node.outputs["Color"], normal_map.inputs["Color"])
            node_tree.links.new(normal_map.outputs["Normal"], normal_input)
            created_maps.append("Normal")

        arrange_material_nodes(material)
        message = f"Created {len(created_maps)} texture(s) at {texture_size}x{texture_size}: {', '.join(created_maps)}." if created_maps else "No textures created; all supported inputs are already connected."
        if skipped_maps:
            message += f" Skipped connected inputs: {', '.join(skipped_maps)}."
        # Only remap UVs when every map is a generated solid color, so existing textures keep their mapping.
        if created_maps and not skipped_maps and _fit_uvs_to_tile_center(obj, material):
            message += f" UVs fitted to the centre {UV_TILE_FILL:.0%} of the tile."
        self.report({"INFO"}, message)
        return {"FINISHED"}
