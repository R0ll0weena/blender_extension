"""Ambient occlusion baking operator."""

import bpy

from ..utils.material_graph import setup_ao_material_graph


class ATLASMAP_OT_bake_ambient_occlusion(bpy.types.Operator):
    bl_idname = "atlasmap.bake_ambient_occlusion"
    bl_label = "Bake Ambient Occlusion"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "The active object must be a mesh.")
            return {"CANCELLED"}
        if context.scene.atlasmap_ao_reunwrap_uv1:
            result = bpy.ops.atlasmap.smart_unwrap_uv1()
            if "FINISHED" not in result:
                self.report({"ERROR"}, "Could not re-unwrap UV1; AO bake cancelled.")
                return {"CANCELLED"}
        if len(obj.data.uv_layers) < 2:
            self.report({"ERROR"}, "Unwrap UV1 before baking ambient occlusion.")
            return {"CANCELLED"}
        if not any(slot.material for slot in obj.material_slots):
            self.report({"ERROR"}, "Assign a material to the active mesh before baking.")
            return {"CANCELLED"}

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
        removed_links = []
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

        previous_mode = obj.mode
        previous_active_object = context.view_layer.objects.active
        previous_selected_objects = list(context.selected_objects)
        previous_engine = scene.render.engine
        previous_samples = scene.cycles.samples
        previous_margin = scene.render.bake.margin
        result = {"CANCELLED"}
        bake_error = None
        try:
            if previous_mode != "OBJECT":
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
                        removed_links.append((link.from_socket, link.to_socket, link.to_node.id_data.links))
                        link.to_node.id_data.links.remove(link)
            result = bpy.ops.object.bake(type="AO", uv_layer=obj.data.uv_layers[1].name)
        except RuntimeError as error:
            bake_error = str(error)
        finally:
            scene.render.engine = previous_engine
            scene.cycles.samples = previous_samples
            scene.render.bake.margin = previous_margin
            for from_socket, to_socket, links in removed_links:
                links.new(from_socket, to_socket)
            for nodes, active_node in previous_active_nodes:
                nodes.active = active_node
            for node in previous_selected_nodes:
                if node.name in node.id_data.nodes:
                    node.select = True
            for selected_object in context.selected_objects:
                selected_object.select_set(False)
            for selected_object in previous_selected_objects:
                selected_object.select_set(True)
            context.view_layer.objects.active = previous_active_object
            if obj.mode != previous_mode:
                bpy.ops.object.mode_set(mode=previous_mode)

        if bake_error or "FINISHED" not in result:
            for node in created_nodes:
                node.id_data.nodes.remove(node)
            if image_created and image.users == 0:
                bpy.data.images.remove(image)
            self.report({"ERROR"}, f"Ambient occlusion bake failed: {bake_error}" if bake_error else "Ambient occlusion bake did not finish.")
            return {"CANCELLED"}

        image.pack()
        for material, ao_node in material_ao_nodes.items():
            principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
            if principled is not None:
                setup_ao_material_graph(material, principled, image, ao_node, scene.atlasmap_texture_size, obj.data.uv_layers[1].name)
        self.report({"INFO"}, f"Ambient occlusion baked to {image.name} ({resolution}px, {samples} samples).")
        return {"FINISHED"}
