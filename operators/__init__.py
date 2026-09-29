"""AtlasMap Blender operators."""

from .atlasmap import ATLASMAP_OT_combine_materials
from .ambient_occlusion import ATLASMAP_OT_bake_ambient_occlusion
from .convert_textures import ATLASMAP_OT_convert_shader_to_textures
from .resample import ATLASMAP_OT_resample_selected_texture
from .unwrap import ATLASMAP_OT_smart_unwrap_uv1

OPERATOR_CLASSES = (
    ATLASMAP_OT_convert_shader_to_textures,
    ATLASMAP_OT_smart_unwrap_uv1,
    ATLASMAP_OT_resample_selected_texture,
    ATLASMAP_OT_combine_materials,
    ATLASMAP_OT_bake_ambient_occlusion,
)
