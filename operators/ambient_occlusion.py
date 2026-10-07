"""Ambient occlusion baking operator."""

import bpy

from ..utils.material_graph import setup_ao_material_graph
from ..utils.node_layout import arrange_material_nodes


class ATLASMAP_OT_bake_ambient_occlusion(bpy.types.Operator):
    bl_idname = "atlasmap.bake_ambient_occlusion"
    bl_label = "Bake Ambient Occlusion"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not self._prepare(context):
            return {"CANCELLED"}
        result = {"CANCELLED"}
        bake_error = None
        try:
            result = bpy.ops.object.bake(type="AO", uv_layer=self._obj.data.uv_layers[1].name)
        except RuntimeError as error:
            bake_error = str(error)
        self._restore(context)
        if bake_error or "FINISHED" not in result:
            return self._fail(f"Ambient occlusion bake failed: {bake_error}" if bake_error else "Ambient occlusion bake did not finish.")
        return self._finish(context)

    def invoke(self, context, event):
        if bpy.app.is_job_running("OBJECT_BAKE"):
            self.report({"ERROR"}, "A bake is already running.")
            return {"CANCELLED"}
        if not self._prepare(context):
            return {"CANCELLED"}
        self._bake_state = None
        bpy.app.handlers.object_bake_complete.append(self._on_bake_complete)
        bpy.app.handlers.object_bake_cancel.append(self._on_bake_cancel)
        try:
            result = bpy.ops.object.bake("INVOKE_DEFAULT", type="AO", uv_layer=self._obj.data.uv_layers[1].name)
        except RuntimeError as error:
            self._remove_handlers()
            self._restore(context)
            return self._fail(f"Ambient occlusion bake failed: {error}")
        if "RUNNING_MODAL" not in result:
            self._remove_handlers()
            self._restore(context)
            return self._fail("Ambient occlusion bake could not be started.")
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if self._bake_state is None and bpy.app.is_job_running("OBJECT_BAKE"):
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        self._remove_handlers()
        cancelled = self._bake_state == "CANCEL"
        # A failed bake also fires the complete handler, so check that the image was written.
        baked = not cancelled and self._image.is_dirty
        self._restore(context)
        if cancelled:
            self._discard()
            self.report({"WARNING"}, "Ambient occlusion bake cancelled.")
            return {"CANCELLED"}
        if not baked:
            return self._fail("Ambient occlusion bake did not finish.")
        return self._finish(context)

    def _on_bake_complete(self, *args):
        self._bake_state = "COMPLETE"

    def _on_bake_cancel(self, *args):
        self._bake_state = "CANCEL"

    def _remove_handlers(self):
        for handlers, callback in (
            (bpy.app.handlers.object_bake_complete, self._on_bake_complete),
            (bpy.app.handlers.object_bake_cancel, self._on_bake_cancel),
        ):
            if callback in handlers:
                handlers.remove(callback)

    def _prepare(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "The active object must be a mesh.")
            return False
        if context.scene.atlasmap_ao_reunwrap_uv1:
            result = bpy.ops.atlasmap.smart_unwrap_uv1()
            if "FINISHED" not in result:
                self.report({"ERROR"}, "Could not unwrap UV1; AO bake cancelled.")
                return False
        if len(obj.data.uv_layers) < 2:
            self.report({"ERROR"}, "Unwrap UV1 before baking ambient occlusion.")
            return False
        if not any(slot.material for slot in obj.material_slots):
            self.report({"ERROR"}, "Assign a material to the active mesh before baking.")
            return False

        scene = context.scene
        resolution = scene.atlasmap_ao_texture_size
        samples = scene.atlasmap_ao_cycles_samples
        image_name = f"{obj.name}_AO"
        image = bpy.data.images.get(image_name)
        image_created = image is None
        if image is None:
            image = bpy.data.images.new(image_name, width=resolution, height=resolution, alpha=False, float_buffer=False)
        elif image.size[:] != (resolution, resolution):
            image.scale(resolution, resolution)
            if image.packed_file is not None:
                image.pack()
        image.colorspace_settings.name = "Non-Color"

        materials = list(dict.fromkeys(slot.material for slot in obj.material_slots if slot.material))
        material_ao_nodes = {}
        created_nodes = []
        previous_active_nodes = []
        previous_selected_nodes = []
        for material in materials:
            material.use_nodes = True
            nodes = material.node_tree.nodes
            previous_active_nodes.append((nodes, nodes.active))
            for node in nodes:
                if node.select:
                    previous_selected_nodes.append(node)
                    node.select = False
            target = next((node for node in nodes if node.type == "TEX_IMAGE" and node.get("atlasmap_ao_target", False) and node.image == image), None)
            if target is None:
                target = nodes.new("ShaderNodeTexImage")
                target.image = image
                target.label = "AO Target"
                target["atlasmap_ao_target"] = True
                created_nodes.append(target)
            target.select = True
            nodes.active = target
            material_ao_nodes[material] = target

        self._obj = obj
        self._image = image
        self._image_created = image_created
        self._resolution = resolution
        self._samples = samples
        self._material_ao_nodes = material_ao_nodes
        self._created_nodes = created_nodes
        self._previous_active_nodes = previous_active_nodes
        self._previous_selected_nodes = previous_selected_nodes
        self._removed_links = []
        self._previous_mode = obj.mode
        self._previous_active_object = context.view_layer.objects.active
        self._previous_selected_objects = list(context.selected_objects)
        self._previous_engine = scene.render.engine
        self._previous_samples = scene.cycles.samples
        self._previous_margin = scene.render.bake.margin

        if self._previous_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for selected_object in context.selected_objects:
            selected_object.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.render.bake.margin = round(scene.atlasmap_ao_island_margin * resolution)
        for ao_node in material_ao_nodes.values():
            for output_socket in ao_node.outputs:
                for link in list(output_socket.links):
                    self._removed_links.append((link.from_socket, link.to_socket, link.to_node.id_data.links))
                    link.to_node.id_data.links.remove(link)
        return True

    def _restore(self, context):
        scene = context.scene
        scene.render.engine = self._previous_engine
        scene.cycles.samples = self._previous_samples
        scene.render.bake.margin = self._previous_margin
        for from_socket, to_socket, links in self._removed_links:
            links.new(from_socket, to_socket)
        for nodes, active_node in self._previous_active_nodes:
            nodes.active = active_node
        for node in self._previous_selected_nodes:
            if node.name in node.id_data.nodes:
                node.select = True
        for selected_object in context.selected_objects:
            selected_object.select_set(False)
        for selected_object in self._previous_selected_objects:
            selected_object.select_set(True)
        context.view_layer.objects.active = self._previous_active_object
        if self._obj.mode != self._previous_mode:
            bpy.ops.object.mode_set(mode=self._previous_mode)

    def _discard(self):
        for node in self._created_nodes:
            node.id_data.nodes.remove(node)
        if self._image_created and self._image.users == 0:
            bpy.data.images.remove(self._image)

    def _fail(self, message):
        self._discard()
        self.report({"ERROR"}, message)
        return {"CANCELLED"}

    def _finish(self, context):
        scene = context.scene
        image = self._image
        obj = self._obj
        image.pack()
        for material, ao_node in self._material_ao_nodes.items():
            principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
            if principled is not None:
                setup_ao_material_graph(material, principled, image, ao_node, scene.atlasmap_texture_size, obj.data.uv_layers[1].name)
                arrange_material_nodes(material)
        self.report({"INFO"}, f"Ambient occlusion baked to {image.name} ({self._resolution}px, {self._samples} samples).")
        return {"FINISHED"}
