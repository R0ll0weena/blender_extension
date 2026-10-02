"""Image resampling utilities."""

import os
from concurrent.futures import as_completed

import numpy as np

WORKER_COUNT = max(1, os.cpu_count() or 1)


class ResampleCancelled(Exception):
    pass


def resolve_resample_method(method, scale_factor):
    if method == "AUTO":
        return "BICUBIC" if scale_factor > 1.0 else "LANCZOS"
    return method


def _kernel(distance, method):
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
        taps = [base_indices + offset for offset in range(-radius + 1, radius + 1)]
        weights = [_kernel(coordinates - indices, method) for indices in taps]
        weight_sum = np.maximum(sum(weights), np.finfo(np.float64).eps)
        shape = (-1,) + (1,) * (source.ndim - 1)
        result = np.zeros((target_size,) + source.shape[1:], dtype=np.float32)
        # Normalized float32 weights keep the per-tap products in float32 instead of float64 temporaries.
        for indices, tap_weights in zip(taps, weights):
            result += source[np.clip(indices, 0, source_size - 1)] * (tap_weights / weight_sum).astype(np.float32).reshape(shape)
    return np.moveaxis(result, 0, axis)


def resample_pixels(pixels, width, height, method):
    pixels = _resample_axis(pixels, width, 1, method)
    return _resample_axis(pixels, height, 0, method)


def scaled_size(width, height, scale_factor):
    return max(1, round(width * scale_factor)), max(1, round(height * scale_factor))


def resample_scaled(pixels, scale_factor, method):
    """Resample an (height, width, 4) array by scale_factor; shared by Scale Textures and atlas downsampling."""
    target_width, target_height = scaled_size(pixels.shape[1], pixels.shape[0], scale_factor)
    return resample_pixels(pixels, target_width, target_height, method)


def _resample_axis_chunked(pixels, target_size, axis, method, executor, report, cancelled):
    """_resample_axis split into row/column bands run on executor threads; NumPy releases the GIL for the heavy work."""
    if pixels.shape[axis] == target_size:
        report(1.0)
        return pixels
    other = 1 - axis
    length = pixels.shape[other]
    step = max(1, -(-length // (WORKER_COUNT * 4)))
    shape = list(pixels.shape)
    shape[axis] = target_size
    result = np.empty(shape, dtype=np.float32)

    def work(start, end):
        if cancelled():
            return
        band = (slice(None),) * other + (slice(start, end),)
        result[band] = _resample_axis(pixels[band], target_size, axis, method)

    futures = [executor.submit(work, start, min(start + step, length)) for start in range(0, length, step)]
    for done, future in enumerate(as_completed(futures), 1):
        future.result()
        report(done / len(futures))
    if cancelled():
        raise ResampleCancelled
    return result


def resample_pixels_parallel(pixels, width, height, method, executor, report=lambda fraction: None, cancelled=lambda: False):
    """Threaded resample_pixels; report(fraction) is called as bands finish. Runs off the main thread, so no bpy access."""
    pixels = _resample_axis_chunked(pixels, width, 1, method, executor, lambda fraction: report(fraction / 2), cancelled)
    return _resample_axis_chunked(pixels, height, 0, method, executor, lambda fraction: report(0.5 + fraction / 2), cancelled)


def read_image_pixels(image):
    width, height = image.size[:]
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape((height, width, 4))


def write_image_pixels(image, pixels):
    target_height, target_width = pixels.shape[:2]
    image.scale(target_width, target_height)
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
    if image.packed_file is not None:
        image.pack()
    return target_width, target_height


def resample_image(image, scale_factor, method):
    return write_image_pixels(image, resample_scaled(read_image_pixels(image), scale_factor, method))
