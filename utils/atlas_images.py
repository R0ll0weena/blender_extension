"""NumPy-based atlas image composition helpers."""

import bpy
import numpy as np


def image_to_array(image):
    values = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape((image.size[1], image.size[0], 4))


def compose_atlas(sources, placements, width, height, category, background_color, margin):
    atlas = np.empty((height, width, 4), dtype=np.float32)
    atlas[:, :] = np.asarray(background_color, dtype=np.float32)
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


def create_packed_image(name, pixels, colorspace):
    height, width, _ = pixels.shape
    image = bpy.data.images.new(name, width=width, height=height, alpha=True, float_buffer=True)
    image.colorspace_settings.name = colorspace
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
    image.pack()
    return image
