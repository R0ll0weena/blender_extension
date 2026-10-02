"""Material atlas generation operator."""

import math
from dataclasses import dataclass

import bpy
import numpy as np

from ..utils.atlas_images import compose_atlas, create_packed_image, image_to_array
from ..utils.atlas_layout import initial_atlas_size, pack_materials
from ..utils.node_layout import arrange_material_nodes
from ..utils.resampling import resample_pixels, resample_scaled, resolve_resample_method, scaled_size
from .texture_channels import _existing_mos_node


@dataclass(eq=False)
class SourceMaterial:
    material: object
    maps: dict
    width: int
    height: int


CATEGORY_SUFFIXES = {
    "Albedo": ("Albedo", "_A"),
    "Metallic": ("Metallic", "_M"),
    "Roughness": ("Roughness", "_R"),
    "Smoothness": ("Smoothness", "_S"),
    "Normal": ("Normal", "_N"),
}


def _linked_image(socket):
    pending = [link.from_node for link in socket.links]
    visited = set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image
        pending.extend(link.from_node for input_socket in node.inputs for link in input_socket.links)
    return None


def _find_map_image(material, principled, category):
    label, suffix = CATEGORY_SUFFIXES[category]
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None and (node.label == label or node.image.name.endswith(suffix)):
            return node.image
    socket_name = "Base Color" if category == "Albedo" else "Metallic" if category == "Metallic" else "Roughness" if category in {"Roughness", "Smoothness"} else "Normal"
    return _linked_image(principled.inputs[socket_name])


def _is_smoothness(principled):
    links = principled.inputs["Roughness"].links
    return bool(links) and links[0].from_node.type == "INVERT"


def _run(operator, material, action):
    if "FINISHED" not in operator():
        raise ValueError(f"Could not {action} for material '{material.name}'.")


def _normalize_materials(obj, context, materials):
    """Generator step: unpack MOS, convert every material to textures, and match each material's R/S type to the Smoothness toggle."""
    scene = context.scene
    previous_index = obj.active_material_index
    previous_channel_pack = scene.atlasmap_channel_pack
    try:
        scene.atlasmap_channel_pack = False
        for number, material in enumerate(materials, 1):
            yield f"Converting {material.name} ({number}/{len(materials)})"
            obj.active_material_index = next(index for index, slot in enumerate(obj.material_slots) if slot.material == material)
            principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None) if material.use_nodes else None
            if principled is None:
                raise ValueError(f"Material '{material.name}' has no Principled BSDF node.")
            if _existing_mos_node(principled) is not None:
                _run(bpy.ops.atlasmap.unpack_mos, material, "unpack the MOS texture")
            _run(bpy.ops.atlasmap.convert_shader_to_textures, material, "convert the shader to textures")
            if _is_smoothness(principled) != scene.atlasmap_convert_to_smoothness:
                _run(bpy.ops.atlasmap.switch_smoothness_roughness, material, "convert Smoothness/Roughness")
    finally:
        scene.atlasmap_channel_pack = previous_channel_pack
        obj.active_material_index = previous_index


FIT_TIP = "Increase Maximum Atlas Map Size, or use the Combine Materials button to downsample."


def _is_uniform(pixels):
    return bool((pixels == pixels[0, 0]).all())


def _object_materials(obj):
    return list(dict.fromkeys(slot.material for slot in obj.material_slots if slot.material))


def _estimate_maps(materials, texture_size):
    """Per material, (width, height, image) for each atlas map from image sizes only; image is None for maps still to be generated."""
    estimates = {}
    for material in materials:
        principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None) if material.use_nodes else None
        if principled is None:
            continue
        maps = []
        for category in ("Albedo", "Metallic", "Roughness", "Normal"):
            image = _find_map_image(material, principled, category)
            maps.append((*image.size, image) if image is not None else (texture_size, texture_size, None))
        estimates[material] = maps
    return estimates


def _tile_size(maps, factor=1.0, solid=None):
    """Tile = largest map. When downsampling, only the scaled non-solid maps set the size; solid maps are stretched losslessly."""
    sizes = [(width, height) for width, height, _ in maps]
    if factor < 1.0:
        real = [scaled_size(width, height, factor) for (width, height, _), is_solid in zip(maps, solid) if not is_solid]
        sizes = real or sizes
    return max(width for width, _ in sizes), max(height for _, height in sizes)


def _fit_problem(tiles, maximum):
    """Return why the tiles cannot fit the maximum atlas size even with perfect packing, or None."""
    total_area = 0
    for material, (width, height) in tiles.items():
        if width > maximum or height > maximum:
            return f"Material '{material.name}' needs a {width}x{height} tile, larger than the maximum atlas size {maximum}."
        total_area += width * height
    if total_area > maximum * maximum:
        side = math.ceil(math.sqrt(total_area))
        return f"Textures need about {side}x{side} pixels but the maximum atlas is {maximum}x{maximum}."
    return None


def _find_downsample_factor(estimates, scene):
    """Return the largest 1/2^k that makes the non-solid textures fit the maximum atlas; solid-color maps keep their size."""
    maximum = scene.atlasmap_maximum_size
    uniform = {}
    solid = {}
    for material, maps in estimates.items():
        for _, _, image in maps:
            if image is not None and image not in uniform:
                uniform[image] = 0 not in image.size[:] and _is_uniform(image_to_array(image))
        solid[material] = [image is None or uniform[image] for _, _, image in maps]
    factor = 1.0
    while True:
        factor /= 2
        tiles = {material: _tile_size(maps, factor, solid[material]) for material, maps in estimates.items()}
        if _fit_problem(tiles, maximum) is None:
            stand_ins = [SourceMaterial(material, {}, width, height) for material, (width, height) in tiles.items()]
            try:
                pack_materials(stand_ins, initial_atlas_size(stand_ins), maximum, scene.atlasmap_atlas_margin)
                return factor
            except ValueError:
                pass
        can_shrink = any(not is_solid and max(width, height) * factor > 1
                         for material, maps in estimates.items() for (width, height, _), is_solid in zip(maps, solid[material]))
        if not can_shrink:
            raise ValueError("Downsampling cannot make the textures fit; increase Maximum Atlas Map Size.")


def _collect_source_materials(obj, context, factor=1.0):
    """Generator step yielding a progress label per unit of work; returns (sources, category)."""
    materials = _object_materials(obj)
    if not materials:
        raise ValueError("The active mesh has no material slots.")
    if factor >= 1.0:
        estimates = _estimate_maps(materials, context.scene.atlasmap_texture_size)
        problem = _fit_problem({material: _tile_size(maps) for material, maps in estimates.items()}, context.scene.atlasmap_maximum_size)
        if problem is not None:
            raise ValueError(f"{problem} {FIT_TIP}")
    yield from _normalize_materials(obj, context, materials)

    category = "Smoothness" if context.scene.atlasmap_convert_to_smoothness else "Roughness"
    sources = []
    for number, material in enumerate(materials, 1):
        yield f"{'Downsampling' if factor < 1.0 else 'Reading'} {material.name} ({number}/{len(materials)})"
        principled = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
        maps = {name: _find_map_image(material, principled, name) for name in ("Albedo", "Metallic", category, "Normal")}
        missing = [name for name, image in maps.items() if image is None]
        if missing:
            raise ValueError(f"Material '{material.name}' is missing texture categories: {', '.join(missing)}.")
        empty = [image.name for image in maps.values() if 0 in image.size[:]]
        if empty:
            raise ValueError(f"Image(s) have no pixel data: {', '.join(empty)}.")
        arrays = {name: image_to_array(image) for name, image in maps.items()}
        uniform = {name: _is_uniform(pixels) for name, pixels in arrays.items()}
        real = [name for name in arrays if not uniform[name]]
        # Textures of one material share a size; smaller ones are resized to the largest.
        # When downsampling, non-solid textures are scaled with the Scale Textures implementation and set the size instead.
        if factor < 1.0 and real:
            method = resolve_resample_method("AUTO", factor)
            for name in real:
                arrays[name] = resample_scaled(arrays[name], factor, method)
            width = max(arrays[name].shape[1] for name in real)
            height = max(arrays[name].shape[0] for name in real)
        else:
            width = max(image.size[0] for image in maps.values())
            height = max(image.size[1] for image in maps.values())
        for name, pixels in arrays.items():
            if pixels.shape[:2] == (height, width):
                continue
            if uniform[name]:
                arrays[name] = np.broadcast_to(pixels[0, 0], (height, width, 4)).copy()
            else:
                method = resolve_resample_method("AUTO", max(width / pixels.shape[1], height / pixels.shape[0]))
                arrays[name] = resample_pixels(pixels, width, height, method)
        sources.append(SourceMaterial(material, arrays, width, height))
    return sources, category


def _build_combined_material(name, images, category):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
    locations = {"Albedo": (-700, 180), "Metallic": (-700, -40), category: (-700, -260), "Normal": (-700, -480)}
    for map_category, image in images.items():
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.label = map_category
        texture.location = locations[map_category]
        if map_category == "Albedo":
            links.new(texture.outputs["Color"], principled.inputs["Base Color"])
        elif map_category == "Metallic":
            links.new(texture.outputs["Color"], principled.inputs["Metallic"])
        elif map_category == "Normal":
            normal_map = nodes.new("ShaderNodeNormalMap")
            normal_map.location = (-400, -480)
            links.new(texture.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
        elif map_category == "Smoothness":
            invert = nodes.new("ShaderNodeInvert")
            invert.inputs["Fac"].default_value = 1.0
            invert.location = (-400, -260)
            links.new(texture.outputs["Color"], invert.inputs["Color"])
            links.new(invert.outputs["Color"], principled.inputs["Roughness"])
        else:
            links.new(texture.outputs["Color"], principled.inputs["Roughness"])
    arrange_material_nodes(material)
    return material


def combine_step_count(obj):
    """Number of labels _combine_steps yields: convert + read per material, packing, four atlases, building."""
    return 2 * len(_object_materials(obj)) + 6


def _combine_steps(obj, context, factor):
    """Generator yielding a progress label before each unit of work; returns the (level, message) report."""
    scene = context.scene
    margin = scene.atlasmap_atlas_margin
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    sources, category = yield from _collect_source_materials(obj, context, factor)
    yield "Packing atlas"
    start_size = initial_atlas_size(sources)
    atlas_width, atlas_height, placements = pack_materials(sources, start_size, scene.atlasmap_maximum_size, margin)

    # Packing succeeded, so pixels can be written now.
    background = tuple(scene.atlasmap_background_color)
    atlas_pixels = {}
    for map_category in ("Albedo", "Metallic", category, "Normal"):
        yield f"Composing {map_category} atlas"
        atlas_pixels[map_category] = compose_atlas(sources, placements, atlas_width, atlas_height, map_category, background, margin)

    yield "Building atlas material"
    atlas_images = {}
    for map_category, pixels in atlas_pixels.items():
        colorspace = "sRGB" if map_category == "Albedo" else "Non-Color"
        atlas_images[map_category] = create_packed_image(f"{obj.name}_Atlas_{map_category}", pixels, colorspace)
    combined = _build_combined_material(f"{obj.name}_AtlasMaterial", atlas_images, category)

    # Remap the UV map the textures sample (the render UV map); UV1 used for AO is left alone.
    uv_layers = obj.data.uv_layers
    uv_layer = next((layer for layer in uv_layers if layer.active_render), uv_layers[0])
    material_by_slot = {index: slot.material for index, slot in enumerate(obj.material_slots)}
    source_by_material = {source.material: source for source in sources}
    tiled = False
    for polygon in obj.data.polygons:
        source = source_by_material.get(material_by_slot.get(polygon.material_index))
        if source is not None:
            placement = placements[source]
            for loop_index in polygon.loop_indices:
                uv = uv_layer.data[loop_index].uv
                tiled = tiled or not (-1e-4 <= uv.x <= 1.0001 and -1e-4 <= uv.y <= 1.0001)
                uv.x = (placement.x + uv.x * placement.width) / atlas_width
                uv.y = (atlas_height - placement.y - placement.height + uv.y * placement.height) / atlas_height
        polygon.material_index = 0
    obj.data.materials.clear()
    obj.data.materials.append(combined)

    message = f"Combined {len(sources)} materials into {atlas_width}x{atlas_height} atlases."
    if factor < 1.0:
        message += f" Textures downsampled to 1/{round(1 / factor)} size."
    if tiled:
        return {"WARNING"}, message + f" Some UVs in '{uv_layer.name}' were outside 0-1 and will sample neighbouring atlas regions."
    return {"INFO"}, message


def _redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def _restore_previous_state(window):
    """After the operator has exited, undo back to the "Before Combine Materials" step."""
    def restore():
        with bpy.context.temp_override(window=window):
            bpy.ops.ed.undo_push(message="Combine Materials (cancelled)")
            bpy.ops.ed.undo()
        return None
    bpy.app.timers.register(restore, first_interval=0.0)


class ATLASMAP_OT_combine_materials(bpy.types.Operator):
    bl_idname = "atlasmap.combine_materials"
    bl_label = "Combine Materials"
    bl_options = {"UNDO"}

    downsample_factor: bpy.props.FloatProperty(default=1.0, min=0.0, max=1.0, options={"HIDDEN", "SKIP_SAVE"})
    prompt_message: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        obj = context.active_object
        if obj is None or obj.type != "MESH" or not obj.data.uv_layers:
            return self.execute(context)
        scene = context.scene
        estimates = _estimate_maps(_object_materials(obj), scene.atlasmap_texture_size)
        problem = _fit_problem({material: _tile_size(maps) for material, maps in estimates.items()}, scene.atlasmap_maximum_size)
        if problem is None:
            return self.execute(context)
        try:
            self.downsample_factor = _find_downsample_factor(estimates, scene)
        except ValueError as error:
            self.report({"ERROR"}, f"{problem} {error}")
            return {"CANCELLED"}
        self.prompt_message = problem
        return context.window_manager.invoke_props_dialog(self, width=520, title="Textures Don't Fit", confirm_text="Downsample")

    def draw(self, context):
        if not self.prompt_message:
            return
        column = self.layout.column(align=True)
        column.label(text=self.prompt_message, icon="ERROR")
        column.label(text=f"Downsample the textures to 1/{round(1 / self.downsample_factor)} size? Solid-color textures are left as is.")
        column.label(text="Downsampling and combining large textures can take minutes.", icon="TIME")
        column.separator()
        column.label(text="Tip: cancel and increase Maximum Atlas Map Size to keep full resolution.", icon="INFO")

    def execute(self, context):
        self.prompt_message = ""
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "The active object must be a mesh.")
            return {"CANCELLED"}
        if not obj.data.uv_layers:
            self.report({"ERROR"}, "The active mesh needs a UV map.")
            return {"CANCELLED"}

        if bpy.app.background or context.window is None:
            # Scripted/headless use: run every step at once.
            steps = _combine_steps(obj, context, self.downsample_factor)
            try:
                while True:
                    next(steps)
            except StopIteration as done:
                self.report(*done.value)
                return {"FINISHED"}
            except (RuntimeError, ValueError) as error:
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}

        bpy.ops.ed.undo_push(message="Before Combine Materials")
        self._window = context.window
        self._steps = _combine_steps(obj, context, self.downsample_factor)
        self._total = combine_step_count(obj)
        self._done = 0
        wm = context.window_manager
        wm.atlasmap_progress_task = "COMBINE"
        wm.atlasmap_progress_running = True
        wm.atlasmap_progress = 0.0
        wm.atlasmap_progress_text = "Starting"
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        _redraw(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            self._steps.close()
            self._finish(context)
            _restore_previous_state(self._window)
            self.report({"WARNING"}, "Combine Materials cancelled; previous state restored.")
            return {"CANCELLED"}
        if event.type != "TIMER":
            # Block other input so the mesh and materials can't change mid-run.
            return {"RUNNING_MODAL"}
        try:
            label = next(self._steps)
        except StopIteration as done:
            self._finish(context)
            self.report(*done.value)
            return {"FINISHED"}
        except (RuntimeError, ValueError) as error:
            self._finish(context)
            _restore_previous_state(self._window)
            self.report({"ERROR"}, f"{error} Previous state restored.")
            return {"CANCELLED"}
        wm = context.window_manager
        wm.atlasmap_progress = min(self._done / self._total, 1.0)
        wm.atlasmap_progress_text = f"{label} ({self._done + 1}/{self._total})"
        self._done += 1
        _redraw(context)
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        wm.atlasmap_progress_running = False
        wm.atlasmap_progress = 0.0
        wm.atlasmap_progress_text = ""
        wm.atlasmap_progress_task = ""
        _redraw(context)
