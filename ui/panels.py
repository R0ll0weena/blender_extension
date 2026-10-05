"""AtlasMap sidebar panels."""

import bpy

from ..operators.ambient_occlusion import ATLASMAP_OT_bake_ambient_occlusion
from ..operators.atlasmap import ATLASMAP_OT_combine_materials, ATLASMAP_OT_combine_shared_materials
from ..operators.color_mapper import ATLASMAP_OT_atlas_to_simple_materials
from ..operators.convert_textures import ATLASMAP_OT_convert_shader_to_textures
from ..operators.resample import ATLASMAP_OT_export_textures, ATLASMAP_OT_resample_selected_texture, selected_texture_images
from ..operators.texture_channels import ATLASMAP_OT_pack_mos, ATLASMAP_OT_switch_smoothness_roughness, ATLASMAP_OT_unpack_mos
from ..operators.unwrap import ATLASMAP_OT_smart_unwrap_uv1


class ATLASMAP_PT_generate_textures(bpy.types.Panel):
    bl_label = "Generate Textures"
    bl_idname = "ATLASMAP_PT_generate_textures"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "atlasmap_texture_size", text="Texture Tile Size")
        if context.scene.atlasmap_texture_size >= 128:
            warning = layout.column(align=True)
            warning.alert = True
            warning.label(text="128 px or more is unnecessary for solid colors.", icon="ERROR")
            warning.label(text="Smaller tiles save atlas space without losing quality.")
        layout.prop(context.scene, "atlasmap_convert_to_smoothness", text="Smoothness")
        layout.prop(context.scene, "atlasmap_channel_pack", text="Channel Pack")
        layout.operator(ATLASMAP_OT_convert_shader_to_textures.bl_idname, icon="TEXTURE")
        layout.separator()
        layout.operator(ATLASMAP_OT_pack_mos.bl_idname)
        layout.operator(ATLASMAP_OT_unpack_mos.bl_idname)
        layout.operator(ATLASMAP_OT_switch_smoothness_roughness.bl_idname)


class ATLASMAP_PT_normalize_textures(bpy.types.Panel):
    bl_label = "Textures"
    bl_idname = "ATLASMAP_PT_normalize_textures"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        material = obj.active_material if obj else None
        if material and material.use_nodes and any(node.type == "TEX_IMAGE" and node.image is not None for node in material.node_tree.nodes):
            header = layout.row(align=True)
            columns = header.split(factor=0.7, align=True)
            columns.label(text="Name")
            columns.label(text="Size")
            layout.template_list("ATLASMAP_UL_material_textures", "", material.node_tree, "nodes", context.scene, "atlasmap_texture_index", rows=4, maxrows=4)
        else:
            layout.label(text="No image textures found", icon="INFO")
        layout.prop(context.scene, "atlasmap_resample_factor", text="Rescale Factor")
        layout.prop(context.scene, "atlasmap_resample_method", text="Resample Method")
        wm = context.window_manager
        if wm.atlasmap_progress_running and wm.atlasmap_progress_task == "RESAMPLE":
            layout.progress(factor=wm.atlasmap_progress, type="BAR", text=wm.atlasmap_progress_text)
            layout.label(text="Press Esc to cancel; finished textures stay resampled.", icon="CANCEL")
        else:
            count = len(selected_texture_images(context.scene, material))
            layout.operator(ATLASMAP_OT_resample_selected_texture.bl_idname, text=f"Resample Textures ({count})", icon="IMAGE")
        layout.operator(ATLASMAP_OT_export_textures.bl_idname, icon="EXPORT")


class ATLASMAP_PT_ambient_occlusion_baking(bpy.types.Panel):
    bl_label = "Ambient Occlusion Baking"
    bl_idname = "ATLASMAP_PT_ambient_occlusion_baking"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        uv_header, uv_body = layout.panel("atlasmap_uv_unwrap_settings", default_closed=True)
        uv_header.label(text="UV Unwrap Settings")
        if uv_body is not None:
            uv_body.prop(context.scene, "atlasmap_uv_angle_limit", text="Angle Limit")
            uv_body.prop(context.scene, "atlasmap_uv_island_margin", text="Island Margin")
            uv_body.prop(context.scene, "atlasmap_uv_margin_method", text="Margin Method")
            uv_body.prop(context.scene, "atlasmap_uv_rotate_method", text="Rotate Method")
            uv_body.prop(context.scene, "atlasmap_uv_area_weight", text="Area Weight")
            uv_body.prop(context.scene, "atlasmap_uv_correct_aspect", text="Correct Aspect")
            uv_body.prop(context.scene, "atlasmap_uv_scale_to_bounds", text="Scale to Bounds")
        layout.operator(ATLASMAP_OT_smart_unwrap_uv1.bl_idname, icon="UV")
        layout.separator()
        layout.prop(context.scene, "atlasmap_ao_reunwrap_uv1", text="Re-unwrap UV1")
        layout.prop(context.scene, "atlasmap_ao_texture_size", text="Texture Size")
        layout.prop(context.scene, "atlasmap_ao_cycles_samples", text="Cycles Samples")
        layout.prop(context.scene, "atlasmap_ao_island_margin", text="Island Margin")
        layout.operator(ATLASMAP_OT_bake_ambient_occlusion.bl_idname, text="Bake Ambient Occlusion", icon="RENDER_STILL")


class ATLASMAP_PT_generate_atlasmap(bpy.types.Panel):
    bl_label = "Generate AtlasMap"
    bl_idname = "ATLASMAP_PT_generate_atlasmap"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"
    bl_order = 4

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "atlasmap_maximum_size", text="Maximum Atlas Map Size")
        layout.prop(context.scene, "atlasmap_atlas_margin", text="Atlas Margin")
        wm = context.window_manager
        if wm.atlasmap_progress_running and wm.atlasmap_progress_task == "COMBINE":
            layout.progress(factor=wm.atlasmap_progress, type="BAR", text=wm.atlasmap_progress_text)
            layout.label(text="Press Esc to cancel and restore the previous state.", icon="CANCEL")
        else:
            layout.operator(ATLASMAP_OT_combine_materials.bl_idname)
            layout.operator(ATLASMAP_OT_combine_shared_materials.bl_idname)


def draw_status_progress(self, context):
    """Status bar progress for atlas generation and resampling, visible even with the AtlasMap panel closed."""
    wm = context.window_manager
    if wm.atlasmap_progress_running:
        row = self.layout.row()
        row.progress(factor=wm.atlasmap_progress, type="BAR", text=wm.atlasmap_progress_text)
        row.label(text="Esc to cancel")


class ATLASMAP_PT_color_mapper(bpy.types.Panel):
    bl_label = "Color Mapper"
    bl_idname = "ATLASMAP_PT_color_mapper"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"
    bl_order = 5

    def draw(self, context):
        self.layout.prop(context.scene, "atlasmap_simple_similarity", text="Similarity Tolerance")
        self.layout.operator(ATLASMAP_OT_atlas_to_simple_materials.bl_idname, icon="COLOR")
