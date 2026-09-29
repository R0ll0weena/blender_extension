"""AtlasMap Blender UI classes."""

from .panels import (
    ATLASMAP_PT_ambient_occlusion_baking,
    ATLASMAP_PT_generate_atlasmap,
    ATLASMAP_PT_generate_textures,
    ATLASMAP_PT_normalize_textures,
)
from .lists import ATLASMAP_UL_material_textures

UI_CLASSES = (
    ATLASMAP_UL_material_textures,
    ATLASMAP_PT_generate_textures,
    ATLASMAP_PT_normalize_textures,
    ATLASMAP_PT_ambient_occlusion_baking,
    ATLASMAP_PT_generate_atlasmap,
)
