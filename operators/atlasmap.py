"""Generate AtlasMap placeholder operator."""

import bpy


class ATLASMAP_OT_combine_materials(bpy.types.Operator):
    bl_idname = "atlasmap.combine_materials"
    bl_label = "Combine Materials"

    def execute(self, context):
        return {"FINISHED"}
