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

        values = (
            ("Base Color", "Base Color", principled.inputs["Base Color"].default_value[:]),
            ("Metallic", "Metallic", principled.inputs["Metallic"].default_value),
            ("Roughness", "Roughness", principled.inputs["Roughness"].default_value),
        )
        texture_size = context.scene.atlasmap_texture_size
        node_tree = material.node_tree
        created_maps = []
        skipped_maps = []
        for index, (map_name, socket_name, value) in enumerate(values):
            input_socket = principled.inputs[socket_name]
            if input_socket.is_linked:
                skipped_maps.append(map_name)
                continue

            color = value if map_name == "Base Color" else (value, value, value, 1.0)
            image = bpy.data.images.new(
                name=f"{material.name}_{map_name.replace(' ', '')}",
                width=texture_size,
                height=texture_size,
                alpha=True,
                float_buffer=True,
            )
            image.colorspace_settings.name = "sRGB" if map_name == "Base Color" else "Non-Color"
            image.pixels.foreach_set(color * (texture_size * texture_size))
            image.pack()

            texture_node = node_tree.nodes.new("ShaderNodeTexImage")
            texture_node.image = image
            texture_node.label = map_name
            texture_node.location = (principled.location.x - 280, principled.location.y - index * 240)
            node_tree.links.new(texture_node.outputs["Color"], input_socket)
            created_maps.append(map_name)

        normal_input = principled.inputs["Normal"]
        if normal_input.is_linked:
            skipped_maps.append("Normal")
        else:
            normal_color = (0.5, 0.5, 1.0, 1.0)
            normal_image = bpy.data.images.new(
                name=f"{material.name}_Normal",
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
            normal_texture.location = (principled.location.x - 560, principled.location.y - 720)

            normal_map = node_tree.nodes.new("ShaderNodeNormalMap")
            normal_map.label = "Normal Map"
            normal_map.space = "TANGENT"
            normal_map.location = (principled.location.x - 280, principled.location.y - 720)
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


class HELLOEXTENSION_PT_panel(bpy.types.Panel):
    """Controls for converting active material values into textures."""

    bl_label = "AtlasMap"
    bl_idname = "HELLOEXTENSION_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AtlasMap"

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "atlasmap_texture_size", text="Texture Size")
        layout.operator(ATLASMAP_OT_convert_shader_to_textures.bl_idname, icon="TEXTURE")


_CLASSES = (
    ATLASMAP_OT_convert_shader_to_textures,
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
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.atlasmap_texture_size
