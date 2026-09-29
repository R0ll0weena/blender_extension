import bpy


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
                        principled.location.x - 560,
                        packed_texture.location.y - 360,
                    )
                    node_tree.links.new(packed_texture.outputs["Color"], invert_node.inputs["Color"])
                    inverted_channels = node_tree.nodes.new("ShaderNodeSeparateColor")
                    inverted_channels.mode = "RGB"
                    inverted_channels.label = "Roughness Channel"
                    inverted_channels.location = (
                        principled.location.x - 280,
                        packed_texture.location.y - 360,
                    )
                    node_tree.links.new(invert_node.outputs["Color"], inverted_channels.inputs["Color"])
                    if not roughness_input.is_linked:
                        node_tree.links.new(inverted_channels.outputs["Blue"], roughness_input)
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


class HELLOEXTENSION_PT_panel(bpy.types.Panel):
    """Controls for converting active material values into textures."""

    bl_label = "AtlasMap"
    bl_idname = "HELLOEXTENSION_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Generated Texture Settings", icon="TEXTURE")
        layout.prop(context.scene, "atlasmap_texture_size", text="Texture Size")
        layout.prop(context.scene, "atlasmap_convert_to_smoothness", text="Smoothness")
        layout.prop(context.scene, "atlasmap_channel_pack", text="Channel Pack")
        layout.operator(ATLASMAP_OT_convert_shader_to_textures.bl_idname, icon="TEXTURE")

        layout.separator()
        layout.label(text="Active Material Textures", icon="IMAGE_DATA")
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


_CLASSES = (
    ATLASMAP_OT_convert_shader_to_textures,
    ATLASMAP_UL_material_textures,
    HELLOEXTENSION_PT_panel,
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
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.atlasmap_texture_index
    del bpy.types.Scene.atlasmap_convert_to_smoothness
    del bpy.types.Scene.atlasmap_channel_pack
    del bpy.types.Scene.atlasmap_texture_size
