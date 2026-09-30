"""NumPy-based atlas image composition helpers."""

import bpy
import numpy as np


def image_to_array(image):
    values = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape((image.size[1], image.size[0], 4))


def compose_atlas(materials, placements, width, height, category, background_color):
    atlas = np.empty((height, width, 4), dtype=np.float32)
    atlas[:, :] = np.asarray(background_color, dtype=np.float32)
    for material in materials:
        placement = placements[material]
        source = image_to_array(material.maps[category].image)
        atlas[placement.y:placement.y + placement.height, placement.x:placement.x + placement.width] = source
    return atlas


def create_packed_image(name, pixels, colorspace):
    height, width, _ = pixels.shape
    image = bpy.data.images.new(name, width=width, height=height, alpha=True, float_buffer=True)
    image.colorspace_settings.name = colorspace
    image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
    image.pack()
    return image
