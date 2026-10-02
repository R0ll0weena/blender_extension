"""NumPy-based atlas image composition helpers."""

import bpy
import numpy as np


def image_to_array(image):
    values = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape((image.size[1], image.size[0], 4))


def _srgb_to_linear(rgb):
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb):
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0.0) ** (1 / 2.4) - 0.055)


def _convert_rgb_in_place(pixels, convert, rows=512):
    """Apply convert to the RGB channels in row bands, so temporaries stay small for large atlases."""
    for start in range(0, pixels.shape[0], rows):
        band = pixels[start:start + rows, :, :3]
        band[...] = convert(band)


def is_high_precision(image):
    """True for loaded texture files stored above 8 bits per channel; generated images are the add-on's own output."""
    return image.source != "GENERATED" and image.is_float


def image_to_linear_array(image):
    """Pixels as the shader sees them: byte images tagged sRGB store encoded values and are decoded here.
    Float buffers are always linear in Blender, whatever their colorspace tag."""
    pixels = image_to_array(image)
    if not image.is_float and image.colorspace_settings.name == "sRGB":
        _convert_rgb_in_place(pixels, _srgb_to_linear)
    return pixels


def compose_atlas(sources, placements, width, height, category, margin):
    # Space no tile covers is never sampled, so it stays transparent black.
    atlas = np.zeros((height, width, 4), dtype=np.float32)
    # Fill each tile's margin by repeating its edge pixels. Neighbours are at least `margin` apart, so splitting
    # the margin floor/ceil across opposite sides keeps paddings from overlapping.
    before, after = margin // 2, margin - margin // 2
    for source in sources:
        placement = placements[source]
        row = height - placement.y - placement.height
        # Array rows run bottom-up: the bottom (array start) gets `after`, the top gets `before`.
        padded = np.pad(source.maps[category], ((after, before), (before, after), (0, 0)), mode="edge")
        start_row, start_col = row - after, placement.x - before
        row_lo, col_lo = max(0, -start_row), max(0, -start_col)
        row_hi = padded.shape[0] - max(0, start_row + padded.shape[0] - height)
        col_hi = padded.shape[1] - max(0, start_col + padded.shape[1] - width)
        atlas[start_row + row_lo:start_row + row_hi, start_col + col_lo:start_col + col_hi] = padded[row_lo:row_hi, col_lo:col_hi]
    for source in sources:
        placement = placements[source]
        # Placements use a top-left origin; Blender pixel rows start at the bottom.
        row = height - placement.y - placement.height
        atlas[row:row + placement.height, placement.x:placement.x + placement.width] = source.maps[category]
    return atlas


def create_packed_image(name, pixels, colorspace, high_precision=False):
    """Create a packed image from linear pixels: a float buffer when high_precision, otherwise 8-bit (a quarter of the memory).
    8-bit sRGB images store encoded values, so pixels are encoded in place for them."""
    height, width, _ = pixels.shape
    image = bpy.data.images.new(name, width=width, height=height, alpha=True, float_buffer=high_precision)
    if len(image.pixels) != width * height * 4:
        # Blender could not allocate the pixel buffer and returned an empty image.
        bpy.data.images.remove(image)
        raise MemoryError(f"Not enough memory for a {width}x{height} atlas image. Lower Maximum Atlas Map Size or downsample the textures.")
    image.colorspace_settings.name = colorspace
    if not high_precision and colorspace == "sRGB":
        _convert_rgb_in_place(pixels, _linear_to_srgb)
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
    image.pack()
    return image
