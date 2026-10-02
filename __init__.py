"""AtlasMap Blender extension entry point."""

import bpy

from .operators import OPERATOR_CLASSES
from .properties import register_properties, unregister_properties
from .ui import UI_CLASSES, draw_status_progress


_CLASSES = OPERATOR_CLASSES + UI_CLASSES


def register():
    register_properties()
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.STATUSBAR_HT_header.append(draw_status_progress)


def unregister():
    bpy.types.STATUSBAR_HT_header.remove(draw_status_progress)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    unregister_properties()
