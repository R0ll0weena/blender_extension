"""Selected texture resampling operator."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import bpy
import numpy as np

from ..utils.resampling import (
    WORKER_COUNT, ResampleCancelled, read_image_pixels, resample_image, resample_pixels_parallel,
    resolve_resample_method, scaled_size, write_image_pixels,
)
from .atlasmap import _redraw


def selected_texture_images(scene, material):
    """Images to resample: the checked list textures, or the active list texture when none are checked."""
    if material is None or not material.use_nodes:
        return []
    nodes = material.node_tree.nodes
    checked = [node for node in nodes if node.type == "TEX_IMAGE" and node.image is not None and node.atlasmap_resample_selected]
    if not checked and 0 <= scene.atlasmap_texture_index < len(nodes):
        active = nodes[scene.atlasmap_texture_index]
        if active.type == "TEX_IMAGE" and active.image is not None:
            checked = [active]
    return list(dict.fromkeys(node.image for node in checked))


class ATLASMAP_OT_resample_selected_texture(bpy.types.Operator):
    bl_idname = "atlasmap.resample_selected_texture"
    bl_label = "Resample Textures"
    bl_description = "Resample the checked textures, or the selected one when none are checked"
    bl_options = {"UNDO"}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj else None
        if material is None or not material.use_nodes:
            self.report({"ERROR"}, "The active object needs a material with nodes enabled.")
            return {"CANCELLED"}
        images = selected_texture_images(context.scene, material)
        if not images:
            self.report({"ERROR"}, "Check or select an image texture in the list first.")
            return {"CANCELLED"}
        unloaded = [image.name for image in images if image.source != "GENERATED" and not image.has_data]
        if unloaded:
            self.report({"ERROR"}, f"Texture image(s) have no pixel data loaded: {', '.join(unloaded)}.")
            return {"CANCELLED"}
        self._factor = context.scene.atlasmap_resample_factor
        self._method = resolve_resample_method(context.scene.atlasmap_resample_method, self._factor)

        if bpy.app.background or context.window is None:
            # Scripted/headless use: resample everything at once.
            try:
                for image in images:
                    resample_image(image, self._factor, self._method)
            except (RuntimeError, ValueError) as error:
                self.report({"ERROR"}, f"Texture resampling failed: {error}")
                return {"CANCELLED"}
            self.report({"INFO"}, f"Resampled {len(images)} texture(s) using {self._method.title()}.")
            return {"FINISHED"}

        self._image_names = [image.name for image in images]
        # Progress is weighted by pixel count so large textures take a matching share of the bar.
        self._weights = [image.size[0] * image.size[1] for image in images]
        self._total_weight = max(sum(self._weights), 1)
        self._done_weight = 0
        self._index = 0
        self._fraction = 0.0
        self._future = None
        self._cancel = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=WORKER_COUNT)
        # Runs each image's resample, which waits on the band tasks in _pool.
        self._runner = ThreadPoolExecutor(max_workers=1)
        wm = context.window_manager
        wm.atlasmap_progress_task = "RESAMPLE"
        wm.atlasmap_progress_running = True
        wm.atlasmap_progress = 0.0
        wm.atlasmap_progress_text = "Starting"
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        _redraw(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            self._finish(context)
            self.report({"WARNING"}, f"Resampling cancelled; {self._index} of {len(self._image_names)} texture(s) resampled.")
            return {"CANCELLED"}
        if event.type != "TIMER":
            # Block other input so the images can't change mid-run.
            return {"RUNNING_MODAL"}
        image = bpy.data.images.get(self._image_names[self._index])
        if image is None:
            self._finish(context)
            self.report({"ERROR"}, f"Texture '{self._image_names[self._index]}' no longer exists.")
            return {"CANCELLED"}
        if self._future is None:
            # bpy is main-thread only: read pixels here and hand the NumPy work to the threads.
            width, height = scaled_size(image.size[0], image.size[1], self._factor)
            self._fraction = 0.0
            self._future = self._runner.submit(
                resample_pixels_parallel, read_image_pixels(image), width, height, self._method, self._pool,
                self._set_fraction, self._cancel.is_set,
            )
        elif self._future.done():
            try:
                write_image_pixels(image, self._future.result())
            except (RuntimeError, ValueError, ResampleCancelled) as error:
                self._finish(context)
                self.report({"ERROR"}, f"Texture resampling failed on {image.name}: {error}")
                return {"CANCELLED"}
            self._future = None
            self._done_weight += self._weights[self._index]
            self._index += 1
            self._fraction = 0.0
            if self._index == len(self._image_names):
                self._finish(context)
                self.report({"INFO"}, f"Resampled {self._index} texture(s) using {self._method.title()}.")
                return {"FINISHED"}
        wm = context.window_manager
        current_weight = self._weights[self._index] * self._fraction
        wm.atlasmap_progress = min((self._done_weight + current_weight) / self._total_weight, 1.0)
        wm.atlasmap_progress_text = f"Resampling {self._image_names[self._index]} ({self._index + 1}/{len(self._image_names)})"
        _redraw(context)
        return {"RUNNING_MODAL"}

    def _set_fraction(self, fraction):
        # Called from the runner thread; only stores a float that the modal reads.
        self._fraction = fraction

    def _finish(self, context):
        self._cancel.set()
        self._runner.shutdown(wait=False, cancel_futures=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        wm.atlasmap_progress_running = False
        wm.atlasmap_progress = 0.0
        wm.atlasmap_progress_text = ""
        wm.atlasmap_progress_task = ""
        _redraw(context)


class ATLASMAP_OT_invert_texture(bpy.types.Operator):
    bl_idname = "atlasmap.invert_texture"
    bl_label = "Invert Texture"
    bl_description = "Invert the colors of the checked textures, or the selected one when none are checked"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj else None
        if material is None or not material.use_nodes:
            self.report({"ERROR"}, "The active object needs a material with nodes enabled.")
            return {"CANCELLED"}
        images = selected_texture_images(context.scene, material)
        if not images:
            self.report({"ERROR"}, "Check or select an image texture in the list first.")
            return {"CANCELLED"}
        unloaded = [image.name for image in images if image.source != "GENERATED" and not image.has_data]
        if unloaded:
            self.report({"ERROR"}, f"Texture image(s) have no pixel data loaded: {', '.join(unloaded)}.")
            return {"CANCELLED"}
        for image in images:
            width, height = image.size[:]
            pixels = np.empty(width * height * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            rgb = pixels.reshape(-1, 4)[:, :3]
            # In place, so no temporaries; alpha is left untouched.
            np.subtract(1.0, rgb, out=rgb)
            image.pixels.foreach_set(pixels)
            if image.packed_file is not None:
                image.pack()
        self.report({"INFO"}, f"Inverted {len(images)} texture(s).")
        return {"FINISHED"}


EXPORT_EXTENSIONS = {
    "PNG": ".png", "JPEG": ".jpg", "TARGA": ".tga", "TARGA_RAW": ".tga", "OPEN_EXR": ".exr",
    "TIFF": ".tif", "HDR": ".hdr", "BMP": ".bmp", "WEBP": ".webp",
}


class ATLASMAP_OT_export_textures(bpy.types.Operator):
    bl_idname = "atlasmap.export_textures"
    bl_label = "Export Textures"
    bl_description = "Save all image textures of the active material to a folder"

    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
        obj = context.active_object
        material = obj.active_material if obj else None
        if material is None or not material.use_nodes:
            self.report({"ERROR"}, "The active object needs a material with nodes enabled.")
            return {"CANCELLED"}
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj else None
        if material is None or not material.use_nodes:
            self.report({"ERROR"}, "The active object needs a material with nodes enabled.")
            return {"CANCELLED"}
        images = list(dict.fromkeys(
            node.image for node in material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image is not None
        ))
        if not images:
            self.report({"ERROR"}, "The active material has no image textures.")
            return {"CANCELLED"}
        directory = bpy.path.abspath(self.directory)
        if not os.path.isdir(directory):
            self.report({"ERROR"}, f"Folder does not exist: {directory}")
            return {"CANCELLED"}

        exported, failed = 0, []
        for image in images:
            name = bpy.path.clean_name(os.path.splitext(image.name)[0])
            path = os.path.join(directory, name + EXPORT_EXTENSIONS.get(image.file_format, ".png"))
            try:
                image.save(filepath=path)
            except RuntimeError:
                failed.append(image.name)
                continue
            exported += 1
        if failed:
            self.report({"WARNING"}, f"Exported {exported} texture(s); failed: {', '.join(failed)}.")
        else:
            self.report({"INFO"}, f"Exported {exported} texture(s) to {directory}.")
        return {"FINISHED"}
