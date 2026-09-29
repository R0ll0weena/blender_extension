import bpy


class HELLOEXTENSION_OT_greet(bpy.types.Operator):
    """Show a greeting in Blender's status area."""

    bl_idname = "hello_extension.greet"
    bl_label = "Say Hello"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self.report({"INFO"}, "Hello from your Blender extension!")
        return {"FINISHED"}


class HELLOEXTENSION_PT_panel(bpy.types.Panel):
    """A small example panel in the 3D View sidebar."""

    bl_label = "Hello Extension"
    bl_idname = "HELLOEXTENSION_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"

    def draw(self, context):
        self.layout.operator(HELLOEXTENSION_OT_greet.bl_idname, icon="INFO")


_CLASSES = (
    HELLOEXTENSION_OT_greet,
    HELLOEXTENSION_PT_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
