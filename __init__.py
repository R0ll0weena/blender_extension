import bpy
import numpy as np


def _resample_kernel(distance, method):
    distance = np.abs(distance)
    if method == "BILINEAR":
        return np.maximum(1.0 - distance, 0.0)
    if method == "BICUBIC":
        a = -0.5
        near = ((a + 2.0) * distance - (a + 3.0)) * distance * distance + 1.0
        far = ((a * distance - 5.0 * a) * distance + 8.0 * a) * distance - 4.0 * a
        return np.where(distance < 1.0, near, np.where(distance < 2.0, far, 0.0))
    if method == "LANCZOS":
        weights = np.sinc(distance) * np.sinc(distance / 3.0)
        return np.where(distance < 3.0, np.where(distance == 0.0, 1.0, weights), 0.0)
    raise ValueError(f"Unsupported resampling method: {method}")


def _resample_axis(pixels, target_size, axis, method):
    source = np.moveaxis(pixels, axis, 0)
    source_size = source.shape[0]
    if source_size == target_size:
        return pixels

    coordinates = (np.arange(target_size, dtype=np.float64) + 0.5) * source_size / target_size - 0.5
    if method == "NEAREST":
        indices = np.clip(np.floor(coordinates + 0.5).astype(np.intp), 0, source_size - 1)
        result = source[indices]
    else:
        radius = {"BILINEAR": 1, "BICUBIC": 2, "LANCZOS": 3}[method]
        base_indices = np.floor(coordinates).astype(np.intp)
        result = np.zeros((target_size,) + source.shape[1:], dtype=np.float32)
        weight_sum = np.zeros(target_size, dtype=np.float64)
        for offset in range(-radius + 1, radius + 1):
            indices = base_indices + offset
            weights = _resample_kernel(coordinates - indices, method)
            clipped_indices = np.clip(indices, 0, source_size - 1)
            result += source[clipped_indices] * weights.reshape((-1,) + (1,) * (source.ndim - 1))
            weight_sum += weights
        result /= np.maximum(weight_sum, np.finfo(np.float64).eps).reshape(
            (-1,) + (1,) * (source.ndim - 1)
        )
    return np.moveaxis(result, 0, axis)


def _resample_image(image, scale_factor, method):
    source_width, source_height = image.size[:]
    target_width = max(1, round(source_width * scale_factor))
    target_height = max(1, round(source_height * scale_factor))
    source_pixels = np.empty(source_width * source_height * 4, dtype=np.float32)
    image.pixels.foreach_get(source_pixels)
    pixels = source_pixels.reshape((source_height, source_width, 4))
    pixels = _resample_axis(pixels, target_width, 1, method)
    pixels = _resample_axis(pixels, target_height, 0, method)

    image.scale(target_width, target_height)
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
    if image.packed_file is not None:
        image.pack()
    return target_width, target_height


def _create_albedo_texture(material, principled, texture_size):
    color = tuple(principled.inputs["Base Color"].default_value)
    image = bpy.data.images.new(
        name=f"{material.name}_A",
        width=texture_size,
        height=texture_size,
        alpha=True,
        float_buffer=True,
    )
    image.colorspace_settings.name = "sRGB"
    image.pixels.foreach_set(color * (texture_size * texture_size))
    image.pack()

    texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture_node.image = image
    texture_node.label = "Albedo"
    texture_node.location = (principled.location.x - 700, principled.location.y + 80)
    return texture_node


def _setup_ao_material_graph(material, principled, ao_image, ao_node, texture_size, uv_layer_name):
    node_tree = material.node_tree
    base_color_input = principled.inputs["Base Color"]
    mix_node = next(
        (node for node in node_tree.nodes if node.get("atlasmap_ao_multiply", False)),
        None,
    )

    albedo_node = None
    if mix_node is not None:
        albedo_links = mix_node.inputs["A"].links
        if albedo_links and albedo_links[0].from_node.type == "TEX_IMAGE":
            albedo_node = albedo_links[0].from_node

    if albedo_node is None:
        upstream_nodes = []
        pending_nodes = [link.from_node for link in base_color_input.links]
        visited = set()
        while pending_nodes:
            node = pending_nodes.pop()
            if node in visited:
                continue
            visited.add(node)
            if node.type == "TEX_IMAGE" and node.image is not None:
                if not node.get("atlasmap_ao_target", False) and node.image != ao_image:
                    upstream_nodes.append(node)
                continue
            pending_nodes.extend(link.from_node for socket in node.inputs for link in socket.links)

        albedo_node = next(
            (
                node
                for node in upstream_nodes
                if node.label == "Albedo" or node.image.name.endswith("_A")
            ),
            upstream_nodes[0] if upstream_nodes else None,
        )

    if albedo_node is None:
        albedo_node = next(
            (
                node
                for node in node_tree.nodes
                if node.type == "TEX_IMAGE"
                and node.image is not None
                and (node.label == "Albedo" or node.image.name.endswith("_A"))
            ),
            None,
        )
    if albedo_node is None:
        albedo_node = _create_albedo_texture(material, principled, texture_size)

    albedo_node.location = (principled.location.x - 700, principled.location.y + 80)
    ao_node.label = "Ambient Occlusion"
    ao_node.location = (albedo_node.location.x, albedo_node.location.y - 320)
    uv_node = next(
        (node for node in node_tree.nodes if node.get("atlasmap_ao_uv_map", False)),
        None,
    )
    if uv_node is None:
        uv_node = node_tree.nodes.new("ShaderNodeUVMap")
        uv_node["atlasmap_ao_uv_map"] = True
    uv_node.uv_map = uv_layer_name
    uv_node.location = (ao_node.location.x - 260, ao_node.location.y)

    if mix_node is None:
        mix_node = node_tree.nodes.new("ShaderNodeMix")
        mix_node["atlasmap_ao_multiply"] = True
    mix_node.data_type = "RGBA"
    mix_node.blend_type = "MULTIPLY"
    mix_node.inputs["Factor"].default_value = 1.0
    mix_node.location = (principled.location.x - 300, principled.location.y + 80)

    for socket in (mix_node.inputs["A"], mix_node.inputs["B"]):
        for link in list(socket.links):
            node_tree.links.remove(link)
    for link in list(base_color_input.links):
        node_tree.links.remove(link)
    node_tree.links.new(albedo_node.outputs["Color"], mix_node.inputs["A"])
    node_tree.links.new(ao_node.outputs["Color"], mix_node.inputs["B"])
    node_tree.links.new(mix_node.outputs["Result"], base_color_input)
    for link in list(ao_node.inputs["Vector"].links):
        node_tree.links.remove(link)
    node_tree.links.new(uv_node.outputs["UV"], ao_node.inputs["Vector"])


class ATLASMAP_OT_convert_shader_to_textures(bpy.types.Operator):
    """Create solid-color textures from the active material's Principled BSDF."""

    bl_idname = "atlasmap.convert_shader_to_textures"
    bl_label = "Convert Shader to Textures"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj else None
        if material is None or not material.use_nodes:
            self.report({"ERROR"}, "The active object needs a material with nodes enabled.")
            return {"CANCELLED"}

        principled = next(
            (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
            None,
        )
        if principled is None:
            self.report({"ERROR"}, "No Principled BSDF node was found in the active material.")
            return {"CANCELLED"}

        smoothness_enabled = context.scene.atlasmap_convert_to_smoothness
        channel_pack_enabled = context.scene.atlasmap_channel_pack
        values = [
            ("Albedo", "_A", "Base Color", principled.inputs["Base Color"].default_value[:]),
        ]
        if not channel_pack_enabled:
            values.extend(
                (
                    ("Metallic", "_M", "Metallic", principled.inputs["Metallic"].default_value),
                    (
                        "Smoothness" if smoothness_enabled else "Roughness",
                        "_S" if smoothness_enabled else "_R",
                        "Roughness",
                        principled.inputs["Roughness"].default_value,
                    ),
                )
            )
        texture_size = context.scene.atlasmap_texture_size
        node_tree = material.node_tree
        created_maps = []
        skipped_maps = []
        for index, (map_name, suffix, socket_name, value) in enumerate(values):
            input_socket = principled.inputs[socket_name]
            if input_socket.is_linked:
                skipped_maps.append(map_name)
                continue

            if socket_name == "Roughness" and smoothness_enabled:
                value = 1.0 - value
            if socket_name == "Base Color":
                texture_node = _create_albedo_texture(material, principled, texture_size)
                node_tree.links.new(texture_node.outputs["Color"], input_socket)
                created_maps.append(map_name)
                continue
            color = value if socket_name == "Base Color" else (value, value, value, 1.0)
            image = bpy.data.images.new(
                name=f"{material.name}{suffix}",
                width=texture_size,
                height=texture_size,
                alpha=True,
                float_buffer=True,
            )
            image.colorspace_settings.name = "sRGB" if socket_name == "Base Color" else "Non-Color"
            image.pixels.foreach_set(color * (texture_size * texture_size))
            image.pack()

            texture_node = node_tree.nodes.new("ShaderNodeTexImage")
            texture_node.image = image
            texture_node.label = map_name
            texture_node.location = (principled.location.x - 560, principled.location.y - index * 360)
            if socket_name == "Roughness" and smoothness_enabled:
                invert_node = node_tree.nodes.new("ShaderNodeInvert")
                invert_node.label = "Smoothness Invert"
                invert_node.inputs["Fac"].default_value = 1.0
                invert_node.location = (
                    principled.location.x - 280,
                    principled.location.y - index * 360,
                )
                node_tree.links.new(texture_node.outputs["Color"], invert_node.inputs["Color"])
                node_tree.links.new(invert_node.outputs["Color"], input_socket)
            else:
                node_tree.links.new(texture_node.outputs["Color"], input_socket)
            created_maps.append(map_name)

        mos_created = False
        if channel_pack_enabled:
            metallic_input = principled.inputs["Metallic"]
            roughness_input = principled.inputs["Roughness"]
            if metallic_input.is_linked:
                skipped_maps.append("Metallic")
            if roughness_input.is_linked:
                skipped_maps.append("Smoothness" if smoothness_enabled else "Roughness")

            if not metallic_input.is_linked or not roughness_input.is_linked:
                metallic_value = metallic_input.default_value
                roughness_value = roughness_input.default_value
                packed_blue = 1.0 - roughness_value if smoothness_enabled else roughness_value
                packed_image = bpy.data.images.new(
                    name=f"{material.name}_MOS",
                    width=texture_size,
                    height=texture_size,
                    alpha=True,
                    float_buffer=True,
                )
                packed_image.colorspace_settings.name = "Non-Color"
                packed_image.pixels.foreach_set(
                    (metallic_value, 0.0, packed_blue, 1.0) * (texture_size * texture_size)
                )
                packed_image.pack()

                packed_texture = node_tree.nodes.new("ShaderNodeTexImage")
                packed_texture.image = packed_image
                packed_texture.label = "Metallic / Smoothness" if smoothness_enabled else "Metallic / Roughness"
                packed_texture.location = (
                    principled.location.x - 560,
                    principled.location.y - len(values) * 360,
                )

                separate_color = node_tree.nodes.new("ShaderNodeSeparateColor")
                separate_color.mode = "RGB"
                separate_color.label = "MOS Channels"
                separate_color.location = (
                    principled.location.x - 280,
                    packed_texture.location.y,
                )
                node_tree.links.new(packed_texture.outputs["Color"], separate_color.inputs["Color"])
                if not metallic_input.is_linked:
                    node_tree.links.new(separate_color.outputs["Red"], metallic_input)

                if smoothness_enabled:
                    invert_node = node_tree.nodes.new("ShaderNodeInvert")
                    invert_node.label = "Smoothness Invert"
                    invert_node.inputs["Fac"].default_value = 1.0
                    invert_node.location = (
                        principled.location.x - 280,
                        packed_texture.location.y - 360,
                    )
                    node_tree.links.new(separate_color.outputs["Blue"], invert_node.inputs["Color"])
                    if not roughness_input.is_linked:
                        node_tree.links.new(invert_node.outputs["Color"], roughness_input)
                elif not roughness_input.is_linked:
                    node_tree.links.new(separate_color.outputs["Blue"], roughness_input)
                created_maps.append("MOS")
                mos_created = True

        normal_input = principled.inputs["Normal"]
        if normal_input.is_linked:
            skipped_maps.append("Normal")
        else:
            normal_row = len(values) + (2 if mos_created and smoothness_enabled else int(mos_created))
            normal_color = (0.5, 0.5, 1.0, 1.0)
            normal_image = bpy.data.images.new(
                name=f"{material.name}_N",
                width=texture_size,
                height=texture_size,
                alpha=True,
                float_buffer=True,
            )
            normal_image.colorspace_settings.name = "Non-Color"
            normal_image.pixels.foreach_set(normal_color * (texture_size * texture_size))
            normal_image.pack()

            normal_texture = node_tree.nodes.new("ShaderNodeTexImage")
            normal_texture.image = normal_image
            normal_texture.label = "Normal"
            normal_texture.location = (
                principled.location.x - 560,
                principled.location.y - normal_row * 360,
            )

            normal_map = node_tree.nodes.new("ShaderNodeNormalMap")
            normal_map.label = "Normal Map"
            normal_map.space = "TANGENT"
            normal_map.location = (
                principled.location.x - 280,
                principled.location.y - normal_row * 360,
            )
            node_tree.links.new(normal_texture.outputs["Color"], normal_map.inputs["Color"])
            node_tree.links.new(normal_map.outputs["Normal"], normal_input)
            created_maps.append("Normal")

        if created_maps:
            message = f"Created {len(created_maps)} texture(s) at {texture_size}x{texture_size}: {', '.join(created_maps)}."
        else:
            message = "No textures created; all supported inputs are already connected."
        if skipped_maps:
            message += f" Skipped connected inputs: {', '.join(skipped_maps)}."
        self.report({"INFO"}, message)
        return {"FINISHED"}


class ATLASMAP_OT_smart_unwrap_uv1(bpy.types.Operator):
    """Smart unwrap the active mesh into its second UV layer."""

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


class ATLASMAP_OT_resample_selected_texture(bpy.types.Operator):
    """Resample the selected material image texture in place."""

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
            width, height = _resample_image(
                image,
                context.scene.atlasmap_resample_factor,
                context.scene.atlasmap_resample_method,
            )
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, f"Texture resampling failed: {error}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Resampled {image.name} to {width} x {height}.")
        return {"FINISHED"}


class ATLASMAP_OT_bake_ambient_occlusion(bpy.types.Operator):
    """Bake ambient occlusion into an image using the active mesh's UV1 layer."""

    bl_idname = "atlasmap.bake_ambient_occlusion"
    bl_label = "Bake Ambient Occlusion"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "The active object must be a mesh.")
            return {"CANCELLED"}
        if context.scene.atlasmap_ao_reunwrap_uv1:
            unwrap_result = bpy.ops.atlasmap.smart_unwrap_uv1()
            if "FINISHED" not in unwrap_result:
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
            image = bpy.data.images.new(
                image_name,
                width=resolution,
                height=resolution,
                alpha=False,
                float_buffer=False,
            )
        elif image.size[:] != (resolution, resolution):
            image.scale(resolution, resolution)
            if image.packed_file is not None:
                image.pack()
        image.colorspace_settings.name = "Non-Color"

        materials = list(dict.fromkeys(slot.material for slot in obj.material_slots if slot.material))
        material_ao_nodes = {}
        temporarily_removed_ao_links = []
        created_nodes = []
        previous_active_nodes = []
        previous_selected = []
        for material in materials:
            material.use_nodes = True
            nodes = material.node_tree.nodes
            previous_active_nodes.append((nodes, nodes.active))
            for node in nodes:
                if node.select:
                    previous_selected.append(node)
                    node.select = False
            target_node = next(
                (
                    node
                    for node in nodes
                    if node.type == "TEX_IMAGE"
                    and node.get("atlasmap_ao_target", False)
                    and node.image == image
                ),
                None,
            )
            if target_node is None:
                target_node = nodes.new("ShaderNodeTexImage")
                target_node.image = image
                target_node.label = "AO Target"
                target_node["atlasmap_ao_target"] = True
                created_nodes.append(target_node)
            target_node.select = True
            nodes.active = target_node
            material_ao_nodes[material] = target_node

        previous_mode = obj.mode
        previous_active_object = context.view_layer.objects.active
        previous_selected_objects = [item for item in context.selected_objects]
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
                        temporarily_removed_ao_links.append(
                            (link.from_socket, link.to_socket, link.to_node.id_data.links)
                        )
                        link.to_node.id_data.links.remove(link)
            result = bpy.ops.object.bake(
                type="AO",
                uv_layer=obj.data.uv_layers[1].name,
            )
        except RuntimeError as error:
            bake_error = str(error)
        finally:
            scene.render.engine = previous_engine
            scene.cycles.samples = previous_samples
            scene.render.bake.margin = previous_margin
            for from_socket, to_socket, links in temporarily_removed_ao_links:
                links.new(from_socket, to_socket)
            for nodes, active_node in previous_active_nodes:
                nodes.active = active_node
            for node in previous_selected:
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
            if bake_error:
                self.report({"ERROR"}, f"Ambient occlusion bake failed: {bake_error}")
            else:
                self.report({"ERROR"}, "Ambient occlusion bake did not finish.")
            return {"CANCELLED"}

        image.pack()
        for material, ao_node in material_ao_nodes.items():
            principled = next(
                (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
                None,
            )
            if principled is not None:
                _setup_ao_material_graph(
                    material,
                    principled,
                    image,
                    ao_node,
                    scene.atlasmap_texture_size,
                    obj.data.uv_layers[1].name,
                )
        self.report({"INFO"}, f"Ambient occlusion baked to {image.name} ({resolution}px, {samples} samples).")
        return {"FINISHED"}


class ATLASMAP_UL_material_textures(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.type == "TEX_IMAGE" and item.image is not None:
            columns = layout.split(factor=0.7, align=True)
            columns.label(text=item.image.name, icon="IMAGE_DATA")
            columns.label(text=f"{item.image.size[0]} x {item.image.size[1]}")

    def filter_items(self, context, data, property_name):
        nodes = getattr(data, property_name)
        flags = [
            self.bitflag_filter_item if node.type == "TEX_IMAGE" and node.image is not None else 0
            for node in nodes
        ]
        return flags, []


class ATLASMAP_PT_generate_textures(bpy.types.Panel):
    bl_label = "Generate Textures"
    bl_idname = "ATLASMAP_PT_generate_textures"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"
    bl_order = 0
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "atlasmap_texture_size", text="Texture Size")
        layout.prop(context.scene, "atlasmap_convert_to_smoothness", text="Smoothness")
        layout.prop(context.scene, "atlasmap_channel_pack", text="Channel Pack")
        layout.operator(ATLASMAP_OT_convert_shader_to_textures.bl_idname, icon="TEXTURE")


class ATLASMAP_PT_normalize_textures(bpy.types.Panel):
    bl_label = "Scale Textures"
    bl_idname = "ATLASMAP_PT_normalize_textures"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        material = obj.active_material if obj else None
        if material and material.use_nodes and any(
            node.type == "TEX_IMAGE" and node.image is not None
            for node in material.node_tree.nodes
        ):
            header = layout.row(align=True)
            columns = header.split(factor=0.7, align=True)
            columns.label(text="Name")
            columns.label(text="Size")
            layout.template_list(
                "ATLASMAP_UL_material_textures",
                "",
                material.node_tree,
                "nodes",
                context.scene,
                "atlasmap_texture_index",
                rows=4,
                maxrows=4,
            )
        else:
            layout.label(text="No image textures found", icon="INFO")
        layout.prop(context.scene, "atlasmap_resample_factor", text="Rescale Factor")
        layout.prop(context.scene, "atlasmap_resample_method", text="Resample Method")
        layout.operator(ATLASMAP_OT_resample_selected_texture.bl_idname, icon="IMAGE" )


class ATLASMAP_PT_ambient_occlusion_baking(bpy.types.Panel):
    bl_label = "Ambient Occlusion Baking"
    bl_idname = "ATLASMAP_PT_ambient_occlusion_baking"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        uv_header, uv_body = layout.panel("atlasmap_uv_unwrap_settings", default_closed=True)
        uv_header.label(text="UV Unwrap Settings")
        if uv_body is not None:
            uv_body.prop(context.scene, "atlasmap_uv_angle_limit", text="Angle Limit")
            uv_body.prop(context.scene, "atlasmap_uv_island_margin", text="Island Margin")
            uv_body.prop(context.scene, "atlasmap_uv_margin_method", text="Margin Method")
            uv_body.prop(context.scene, "atlasmap_uv_rotate_method", text="Rotate Method")
            uv_body.prop(context.scene, "atlasmap_uv_area_weight", text="Area Weight")
            uv_body.prop(context.scene, "atlasmap_uv_correct_aspect", text="Correct Aspect")
            uv_body.prop(context.scene, "atlasmap_uv_scale_to_bounds", text="Scale to Bounds")

        layout.operator(ATLASMAP_OT_smart_unwrap_uv1.bl_idname, icon="UV")
        layout.separator()
        layout.prop(context.scene, "atlasmap_ao_reunwrap_uv1", text="Re-unwrap UV1")
        layout.prop(context.scene, "atlasmap_ao_texture_size", text="Texture Size")
        layout.prop(context.scene, "atlasmap_ao_cycles_samples", text="Cycles Samples")
        layout.prop(context.scene, "atlasmap_ao_island_margin", text="Island Margin")
        layout.operator(
            ATLASMAP_OT_bake_ambient_occlusion.bl_idname,
            text="Bake Ambient Occlusion",
            icon="RENDER_STILL",
        )


_CLASSES = (
    ATLASMAP_OT_convert_shader_to_textures,
    ATLASMAP_OT_smart_unwrap_uv1,
    ATLASMAP_OT_resample_selected_texture,
    ATLASMAP_OT_bake_ambient_occlusion,
    ATLASMAP_UL_material_textures,
    ATLASMAP_PT_generate_textures,
    ATLASMAP_PT_normalize_textures,
    ATLASMAP_PT_ambient_occlusion_baking,
)


def register():
    bpy.types.Scene.atlasmap_texture_size = bpy.props.IntProperty(
        name="Texture Size",
        description="Width and height in pixels for each generated texture",
        default=1024,
        min=1,
        max=8192,
    )
    bpy.types.Scene.atlasmap_texture_index = bpy.props.IntProperty(default=0)
    bpy.types.Scene.atlasmap_resample_factor = bpy.props.FloatProperty(
        name="Rescale Factor",
        description="Scale the selected image's width and height by this factor",
        default=0.5,
        min=0.01,
        max=16.0,
        precision=2,
    )
    bpy.types.Scene.atlasmap_resample_method = bpy.props.EnumProperty(
        name="Resample Method",
        items=(
            ("NEAREST", "Nearest", "Nearest-neighbor sampling"),
            ("BILINEAR", "Bilinear", "Linear interpolation"),
            ("BICUBIC", "Bicubic", "Cubic interpolation"),
            ("LANCZOS", "Lanczos", "Lanczos windowed-sinc interpolation"),
        ),
        default="BICUBIC",
    )
    bpy.types.Scene.atlasmap_convert_to_smoothness = bpy.props.BoolProperty(
        name="Smoothness",
        description="Generate an inverted smoothness texture and invert it again for the Principled BSDF",
        default=False,
    )
    bpy.types.Scene.atlasmap_channel_pack = bpy.props.BoolProperty(
        name="Channel Pack",
        description="Pack metallic into red and roughness or smoothness into blue of a single MOS texture",
        default=False,
    )
    bpy.types.Scene.atlasmap_uv_angle_limit = bpy.props.FloatProperty(
        name="Angle Limit",
        description="Maximum face angle before a new UV island is created",
        subtype="ANGLE",
        default=1.151917,
        min=0.0,
        max=1.570796,
    )
    bpy.types.Scene.atlasmap_uv_island_margin = bpy.props.FloatProperty(
        name="Island Margin",
        description="Spacing between generated UV islands",
        default=0.0,
        min=0.0,
        max=1.0,
    )
    bpy.types.Scene.atlasmap_uv_margin_method = bpy.props.EnumProperty(
        name="Margin Method",
        items=(
            ("SCALED", "Scaled", "Scale island spacing with the UV layout"),
            ("ADD", "Add", "Add a fixed spacing between islands"),
            ("FRACTION", "Fraction", "Use a fraction of the UV space"),
        ),
        default="SCALED",
    )
    bpy.types.Scene.atlasmap_uv_rotate_method = bpy.props.EnumProperty(
        name="Rotate Method",
        items=(
            ("AXIS_ALIGNED", "Axis-aligned", "Rotate islands to best fit the UV space"),
            ("AXIS_ALIGNED_X", "Horizontal", "Align islands horizontally"),
            ("AXIS_ALIGNED_Y", "Vertical", "Align islands vertically"),
        ),
        default="AXIS_ALIGNED_Y",
    )
    bpy.types.Scene.atlasmap_uv_area_weight = bpy.props.FloatProperty(
        name="Area Weight",
        description="Weight larger faces when packing UV islands",
        default=0.0,
        min=0.0,
        max=1.0,
    )
    bpy.types.Scene.atlasmap_uv_correct_aspect = bpy.props.BoolProperty(
        name="Correct Aspect",
        description="Account for image aspect ratio during unwrapping",
        default=True,
    )
    bpy.types.Scene.atlasmap_uv_scale_to_bounds = bpy.props.BoolProperty(
        name="Scale to Bounds",
        description="Scale UV islands to fill the UV square",
        default=False,
    )
    bpy.types.Scene.atlasmap_ao_texture_size = bpy.props.IntProperty(
        name="Texture Size",
        description="Bake image width and height",
        default=1024,
        min=16,
        max=16384,
    )
    bpy.types.Scene.atlasmap_ao_reunwrap_uv1 = bpy.props.BoolProperty(
        name="Re-unwrap UV1",
        description="Run Smart UV Project on the second UV layer before each AO bake",
        default=True,
    )
    bpy.types.Scene.atlasmap_ao_cycles_samples = bpy.props.IntProperty(
        name="Cycles Samples",
        description="Cycles samples used for the bake",
        default=64,
        min=1,
        max=65536,
    )
    bpy.types.Scene.atlasmap_ao_island_margin = bpy.props.FloatProperty(
        name="Island Margin",
        description="Bake edge padding as a fraction of the image size",
        default=0.0,
        min=0.0,
        max=0.25,
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.atlasmap_texture_index
    del bpy.types.Scene.atlasmap_resample_factor
    del bpy.types.Scene.atlasmap_resample_method
    del bpy.types.Scene.atlasmap_convert_to_smoothness
    del bpy.types.Scene.atlasmap_channel_pack
    del bpy.types.Scene.atlasmap_texture_size
    del bpy.types.Scene.atlasmap_uv_angle_limit
    del bpy.types.Scene.atlasmap_uv_island_margin
    del bpy.types.Scene.atlasmap_uv_margin_method
    del bpy.types.Scene.atlasmap_uv_rotate_method
    del bpy.types.Scene.atlasmap_uv_area_weight
    del bpy.types.Scene.atlasmap_uv_correct_aspect
    del bpy.types.Scene.atlasmap_uv_scale_to_bounds
    del bpy.types.Scene.atlasmap_ao_texture_size
    del bpy.types.Scene.atlasmap_ao_reunwrap_uv1
    del bpy.types.Scene.atlasmap_ao_cycles_samples
    del bpy.types.Scene.atlasmap_ao_island_margin
