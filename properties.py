"""Scene properties used by AtlasMap."""

import bpy


def _show_selected_texture(scene, context):
    """Show the texture selected in the Scale Textures list in every open, unpinned Image/UV editor."""
    obj = context.active_object
    material = obj.active_material if obj else None
    if material is None or not material.use_nodes:
        return
    nodes = material.node_tree.nodes
    if not 0 <= scene.atlasmap_texture_index < len(nodes):
        return
    node = nodes[scene.atlasmap_texture_index]
    if node.type != "TEX_IMAGE" or node.image is None:
        return
    # In Edit mode the UV editor follows the active texture node, so make it match.
    nodes.active = node
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "IMAGE_EDITOR" and not area.spaces.active.use_image_pin:
                area.spaces.active.image = node.image
                # Blender auto-pins images assigned to a UV editor; undo that so the editor keeps following the list.
                area.spaces.active.use_image_pin = False


def register_properties():
    # Combine Materials / Resample Textures progress (runtime only; window-manager data is not saved in the .blend).
    bpy.types.WindowManager.atlasmap_progress_task = bpy.props.StringProperty()
    bpy.types.WindowManager.atlasmap_progress_running = bpy.props.BoolProperty(default=False)
    bpy.types.WindowManager.atlasmap_progress = bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0)
    bpy.types.WindowManager.atlasmap_progress_text = bpy.props.StringProperty()
    bpy.types.Scene.atlasmap_texture_size = bpy.props.IntProperty(
        name="Texture Size", description="Width and height in pixels for each generated texture",
        default=1024, min=1, max=8192,
    )
    bpy.types.Scene.atlasmap_texture_index = bpy.props.IntProperty(default=0, update=_show_selected_texture)
    bpy.types.ShaderNodeTexImage.atlasmap_resample_selected = bpy.props.BoolProperty(
        name="Resample", description="Include this texture when resampling", default=False,
    )
    bpy.types.Scene.atlasmap_resample_factor = bpy.props.FloatProperty(
        name="Rescale Factor", description="Scale the selected images' width and height by this factor",
        default=0.5, min=0.01, max=16.0, precision=2,
    )
    bpy.types.Scene.atlasmap_resample_method = bpy.props.EnumProperty(
        name="Resample Method",
        items=(
            ("AUTO", "Auto", "Automatically uses Lanczos if downsampling, and Bicubic if upsampling"),
            ("NEAREST", "Nearest", "Sharp/Pixel art."),
            ("BILINEAR", "Bilinear", "Fast, standard linear blending."),
            ("BICUBIC", "Bicubic", "Smooth gradients (great for upsampling)."),
            ("LANCZOS", "Lanczos", "Sharp detail preservation (great for downsampling)."),
        ),
        default="AUTO",
    )
    bpy.types.Scene.atlasmap_maximum_size = bpy.props.IntProperty(
        name="Maximum Atlas Map Size", description="Maximum width or height of the generated material atlas",
        default=4096, min=16, max=16384,
    )
    bpy.types.Scene.atlasmap_atlas_margin = bpy.props.IntProperty(
        name="Atlas Margin", description="Minimum gap in pixels between packed textures",
        default=4, min=0, max=256,
    )
    bpy.types.Scene.atlasmap_simple_similarity = bpy.props.FloatProperty(
        name="Similarity Tolerance",
        description="Reuse an already created simple material when albedo (sRGB), metallic and roughness all differ by at most this percentage",
        subtype="PERCENTAGE", default=5.0, min=0.0, max=50.0, precision=1, step=50,
    )
    bpy.types.Scene.atlasmap_convert_to_smoothness = bpy.props.BoolProperty(
        name="Smoothness", description="Generate an inverted smoothness texture and invert it again for the Principled BSDF",
        default=False,
    )
    bpy.types.Scene.atlasmap_channel_pack = bpy.props.BoolProperty(
        name="Channel Pack", description="Pack metallic into red and roughness or smoothness into blue of a single MOS texture",
        default=False,
    )
    bpy.types.Scene.atlasmap_uv_angle_limit = bpy.props.FloatProperty(
        name="Angle Limit", description="Maximum face angle before a new UV island is created",
        subtype="ANGLE", default=1.151917, min=0.0, max=1.570796,
    )
    bpy.types.Scene.atlasmap_uv_island_margin = bpy.props.FloatProperty(
        name="Island Margin", description="Spacing between generated UV islands",
        default=0.0, min=0.0, max=1.0,
    )
    bpy.types.Scene.atlasmap_uv_margin_method = bpy.props.EnumProperty(
        name="Margin Method",
        items=(("SCALED", "Scaled", "Scale island spacing with the UV layout"), ("ADD", "Add", "Add a fixed spacing between islands"), ("FRACTION", "Fraction", "Use a fraction of the UV space")),
        default="SCALED",
    )
    bpy.types.Scene.atlasmap_uv_rotate_method = bpy.props.EnumProperty(
        name="Rotate Method",
        items=(("AXIS_ALIGNED", "Axis-aligned", "Rotate islands to best fit the UV space"), ("AXIS_ALIGNED_X", "Horizontal", "Align islands horizontally"), ("AXIS_ALIGNED_Y", "Vertical", "Align islands vertically")),
        default="AXIS_ALIGNED_Y",
    )
    bpy.types.Scene.atlasmap_uv_area_weight = bpy.props.FloatProperty(
        name="Area Weight", description="Weight larger faces when packing UV islands", default=0.0, min=0.0, max=1.0,
    )
    bpy.types.Scene.atlasmap_uv_correct_aspect = bpy.props.BoolProperty(
        name="Correct Aspect", description="Account for image aspect ratio during unwrapping", default=True,
    )
    bpy.types.Scene.atlasmap_uv_scale_to_bounds = bpy.props.BoolProperty(
        name="Scale to Bounds", description="Scale UV islands to fill the UV square", default=False,
    )
    bpy.types.Scene.atlasmap_ao_texture_size = bpy.props.IntProperty(
        name="Texture Size", description="Bake image width and height", default=1024, min=16, max=16384,
    )
    bpy.types.Scene.atlasmap_ao_reunwrap_uv1 = bpy.props.BoolProperty(
        name="Re-unwrap UV1", description="Run Smart UV Project on the second UV layer before each AO bake", default=True,
    )
    bpy.types.Scene.atlasmap_ao_cycles_samples = bpy.props.IntProperty(
        name="Cycles Samples", description="Cycles samples used for the bake", default=64, min=1, max=65536,
    )
    bpy.types.Scene.atlasmap_ao_island_margin = bpy.props.FloatProperty(
        name="Island Margin", description="Bake edge padding as a fraction of the image size", default=0.0, min=0.0, max=0.25,
    )


def unregister_properties():
    names = (
        "atlasmap_texture_index", "atlasmap_resample_factor", "atlasmap_resample_method",
        "atlasmap_maximum_size", "atlasmap_atlas_margin",
        "atlasmap_convert_to_smoothness", "atlasmap_channel_pack", "atlasmap_texture_size",
        "atlasmap_uv_angle_limit", "atlasmap_uv_island_margin", "atlasmap_uv_margin_method",
        "atlasmap_uv_rotate_method", "atlasmap_uv_area_weight", "atlasmap_uv_correct_aspect",
        "atlasmap_uv_scale_to_bounds", "atlasmap_ao_texture_size", "atlasmap_ao_reunwrap_uv1",
        "atlasmap_ao_cycles_samples", "atlasmap_ao_island_margin", "atlasmap_simple_similarity",
    )
    for name in names:
        delattr(bpy.types.Scene, name)
    del bpy.types.ShaderNodeTexImage.atlasmap_resample_selected
    for name in ("atlasmap_progress_task", "atlasmap_progress_running", "atlasmap_progress", "atlasmap_progress_text"):
        delattr(bpy.types.WindowManager, name)
