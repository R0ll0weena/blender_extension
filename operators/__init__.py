"""AtlasMap Blender operators."""

from .atlasmap import ATLASMAP_OT_combine_materials, ATLASMAP_OT_combine_shared_materials
from .ambient_occlusion import ATLASMAP_OT_bake_ambient_occlusion
from .color_mapper import ATLASMAP_OT_atlas_to_simple_materials
from .convert_textures import ATLASMAP_OT_convert_shader_to_textures
from .resample import ATLASMAP_OT_export_textures, ATLASMAP_OT_invert_texture, ATLASMAP_OT_resample_selected_texture
from .texture_channels import ATLASMAP_OT_pack_mos, ATLASMAP_OT_pack_orm, ATLASMAP_OT_switch_smoothness_roughness, ATLASMAP_OT_unpack_mos, ATLASMAP_OT_unpack_orm
from .unwrap import ATLASMAP_OT_smart_unwrap_uv1

OPERATOR_CLASSES = (
    ATLASMAP_OT_convert_shader_to_textures,
    ATLASMAP_OT_pack_mos,
    ATLASMAP_OT_unpack_mos,
    ATLASMAP_OT_pack_orm,
    ATLASMAP_OT_unpack_orm,
    ATLASMAP_OT_switch_smoothness_roughness,
    ATLASMAP_OT_smart_unwrap_uv1,
    ATLASMAP_OT_resample_selected_texture,
    ATLASMAP_OT_invert_texture,
    ATLASMAP_OT_export_textures,
    ATLASMAP_OT_combine_materials,
    ATLASMAP_OT_combine_shared_materials,
    ATLASMAP_OT_bake_ambient_occlusion,
    ATLASMAP_OT_atlas_to_simple_materials,
)
