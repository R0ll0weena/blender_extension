"""Boolean-grid atlas layout helpers."""

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AtlasPlacement:
    """Texture region in the atlas, with a top-left origin."""
    material: object
    x: int
    y: int
    width: int
    height: int


def next_power_of_two(value):
    return 1 if value <= 1 else 2 ** (value - 1).bit_length()


def initial_atlas_size(sources):
    """Return the first size in the growth sequence (1x1, 2x1, 2x2, 4x2, ...) whose area holds all textures.

    Margins are left out because they are clipped at the atlas edges; packing grows the atlas if they don't fit.
    """
    area = sum(source.width * source.height for source in sources)
    side = next_power_of_two(math.ceil(math.sqrt(area)))
    return (side, side // 2) if side > 1 and side * (side // 2) >= area else (side, side)


def candidate_sizes(start_size, maximum_size):
    """Grow one side at a time by a factor of 2: 256x256, 512x256, 512x512, ..."""
    width, height = start_size
    while width <= maximum_size and height <= maximum_size:
        yield width, height
        if width == height:
            width *= 2
        else:
            height *= 2


def _try_pack(ordered, atlas_width, atlas_height, margin):
    """Place each texture at the first free candidate corner, top-to-bottom then left-to-right.

    Candidates are (0, 0) plus the right and bottom edges (+ margin) of every placed texture. Each texture
    reserves its size plus the margin to the right and below (clipped at the atlas edge) in the bool array.
    """
    occupied = np.zeros((atlas_height, atlas_width), dtype=np.bool_)
    xs, ys = {0}, {0}
    placements = {}
    for source in ordered:
        width, height = source.width, source.height
        found = None
        for y in sorted(ys):
            if y + height > atlas_height:
                break
            for x in sorted(xs):
                if x + width > atlas_width:
                    break
                if not occupied[y:y + height + margin, x:x + width + margin].any():
                    found = (x, y)
                    break
            if found is not None:
                break
        if found is None:
            return None
        x, y = found
        occupied[y:y + height + margin, x:x + width + margin] = True
        xs.add(x + width + margin)
        ys.add(y + height + margin)
        placements[source] = AtlasPlacement(source.material, x, y, width, height)
    return placements


def pack_materials(sources, start_size, maximum_size, margin):
    ordered = sorted(sources, key=lambda source: (max(source.width, source.height), source.width * source.height), reverse=True)
    for atlas_width, atlas_height in candidate_sizes(start_size, maximum_size):
        placements = _try_pack(ordered, atlas_width, atlas_height, margin)
        if placements is not None:
            return atlas_width, atlas_height, placements
    raise ValueError(f"Textures need a larger atlas than the maximum size {maximum_size}.")
