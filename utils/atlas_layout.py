"""Boolean-grid atlas layout helpers."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AtlasPlacement:
    material: object
    x: int
    y: int
    width: int
    height: int


def next_power_of_two(value):
    return 1 if value <= 1 else 2 ** (value - 1).bit_length()


def initial_atlas_size(materials, category_names):
    category_areas = {category: 0 for category in category_names}
    for material in materials:
        for category in category_names:
            image = material.maps[category]
            category_areas[category] += image.size[0] * image.size[1]
    return max(next_power_of_two(area) for area in category_areas.values())


def candidate_sizes(start_size, maximum_size):
    width = height = start_size
    while width <= maximum_size and height <= maximum_size:
        yield width, height
        if width == height:
            width *= 2
        else:
            height *= 2


def pack_materials(materials, start_size, maximum_size, margin_pixels, step):
    ordered = sorted(
        materials,
        key=lambda material: max(tuple(material.maps[category].size) for category in material.maps),
        reverse=True,
    )
    for atlas_width, atlas_height in candidate_sizes(start_size, maximum_size):
        occupied = np.zeros((atlas_height, atlas_width), dtype=np.bool_)
        placements = {}
        valid = True
        for material in ordered:
            width, height = material.maps["Albedo"].size
            padded_width = width + margin_pixels * 2
            padded_height = height + margin_pixels * 2
            found = None
            for y in range(0, atlas_height - padded_height + 1, max(1, step)):
                for x in range(0, atlas_width - padded_width + 1, max(1, step)):
                    if not occupied[y:y + padded_height, x:x + padded_width].any():
                        occupied[y:y + padded_height, x:x + padded_width] = True
                        found = (x + margin_pixels, y + margin_pixels)
                        break
                if found is not None:
                    break
            if found is None:
                valid = False
                break
            placements[material] = AtlasPlacement(material, found[0], found[1], width, height)
        if valid:
            return atlas_width, atlas_height, placements
    raise ValueError(f"Textures do not fit within the maximum atlas size of {maximum_size}.")
