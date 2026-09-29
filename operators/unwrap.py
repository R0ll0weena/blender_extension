"""UV1 Smart UV Project operator."""

import bpy


class ATLASMAP_OT_smart_unwrap_uv1(bpy.types.Operator):
    bl_idname = "atlasmap.smart_unwrap_uv1"
    bl_label = "Unwrap UV1"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "The active object must be a mesh.")
            return {"CANCELLED"}
        if not obj.data.polygons:
            self.report({"ERROR"}, "The active mesh has no faces to unwrap.")
            return {"CANCELLED"}
        previous_mode = obj.mode
        try:
            if previous_mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            uv_layers = obj.data.uv_layers
            if len(uv_layers) == 0:
                uv_layers.new(name="UVMap")
            if len(uv_layers) == 1:
                uv_layers.new(name="UV1")
            uv_layers.active_index = 1
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            result = bpy.ops.uv.smart_project(
                angle_limit=context.scene.atlasmap_uv_angle_limit,
                margin_method=context.scene.atlasmap_uv_margin_method,
                island_margin=context.scene.atlasmap_uv_island_margin,
                rotate_method=context.scene.atlasmap_uv_rotate_method,
                area_weight=context.scene.atlasmap_uv_area_weight,
                correct_aspect=context.scene.atlasmap_uv_correct_aspect,
                scale_to_bounds=context.scene.atlasmap_uv_scale_to_bounds,
            )
        except RuntimeError as error:
            self.report({"ERROR"}, f"Smart UV Project failed: {error}")
            return {"CANCELLED"}
        finally:
            if obj.mode != previous_mode:
                bpy.ops.object.mode_set(mode=previous_mode)
        if "FINISHED" not in result:
            self.report({"ERROR"}, "Smart UV Project did not finish.")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Smart unwrapped {obj.name} into UV layer 2.")
        return {"FINISHED"}
