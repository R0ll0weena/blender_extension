"""Image resampling utilities."""

import numpy as np


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
        result = np.zeros((target_size,) + source.shape[1:], dtype=np.float32)
        weight_sum = np.zeros(target_size, dtype=np.float64)
        for offset in range(-radius + 1, radius + 1):
            indices = base_indices + offset
            weights = _kernel(coordinates - indices, method)
            clipped_indices = np.clip(indices, 0, source_size - 1)
            result += source[clipped_indices] * weights.reshape((-1,) + (1,) * (source.ndim - 1))
            weight_sum += weights
        result /= np.maximum(weight_sum, np.finfo(np.float64).eps).reshape((-1,) + (1,) * (source.ndim - 1))
    return np.moveaxis(result, 0, axis)


def resample_image(image, scale_factor, method):
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
