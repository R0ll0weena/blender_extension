"""AtlasMap Blender extension entry point."""

import bpy

from .operators import OPERATOR_CLASSES
from .properties import register_properties, unregister_properties
from .ui import UI_CLASSES


_CLASSES = OPERATOR_CLASSES + UI_CLASSES


def register():
    register_properties()
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    unregister_properties()
