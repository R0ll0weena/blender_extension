"""Selected texture resampling operator."""

import bpy

from ..utils.resampling import resolve_resample_method, resample_image


class ATLASMAP_OT_resample_selected_texture(bpy.types.Operator):
    bl_idname = "atlasmap.resample_selected_texture"
    bl_label = "Resample Texture"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj else None
        if material is None or not material.use_nodes:
            self.report({"ERROR"}, "The active object needs a material with nodes enabled.")
            return {"CANCELLED"}
        texture_index = context.scene.atlasmap_texture_index
        nodes = material.node_tree.nodes
        if texture_index < 0 or texture_index >= len(nodes):
            self.report({"ERROR"}, "Select an image texture from the list first.")
            return {"CANCELLED"}
        texture_node = nodes[texture_index]
        if texture_node.type != "TEX_IMAGE" or texture_node.image is None:
            self.report({"ERROR"}, "The selected node has no image texture to resample.")
            return {"CANCELLED"}
        image = texture_node.image
        if image.source != "GENERATED" and not image.has_data:
            self.report({"ERROR"}, "The selected texture image has no pixel data loaded.")
            return {"CANCELLED"}
        try:
            factor = context.scene.atlasmap_resample_factor
            method = resolve_resample_method(context.scene.atlasmap_resample_method, factor)
            width, height = resample_image(image, factor, method)
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, f"Texture resampling failed: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Resampled {image.name} to {width} x {height} using {method.title()}.")
        return {"FINISHED"}
