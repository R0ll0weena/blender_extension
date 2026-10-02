# Blender Atlasmap Extension

A Blender extension that collects several tools used when optimizing 3D assets for real-time performance. Each tool covers one step of that workflow: baking shader values to textures, packing channel maps, baking ambient occlusion, resampling textures, and combining materials into texture atlases. The tools can be used on their own or chained together.

**Target:** Blender 4.2 or newer (built as a Blender extension, see `blender_manifest.toml`).

## Purpose

Assets authored in Blender often use several materials, each driven by Principled BSDF values instead of image textures. Game engines and other real-time targets prefer the opposite: as few materials and draw calls as possible, with everything stored in compact, well-sized textures. Getting there usually means a series of manual steps, and AtlasMap automates them inside Blender.

The biggest impact comes from **atlas map generation**. A mesh with many materials needs one draw call per material. **Generate Atlasmap** merges all of them into a single material: the textures are packed into shared atlases, and the UVs are remapped to match. The mesh is then drawn with one material instead of many. The other tools support this step. They convert shader values to textures, so every material can go into the atlas. They also bake AO, pack channels to save texture memory, and resample textures to the right resolution.

This is a portfolio project, focused on Blender's Python API, image processing with NumPy, and safe, non-destructive manipulation of material node graphs.

This is a portfolio project, focused on Blender's Python API, image processing with NumPy, and safe, non-destructive manipulation of material node graphs.

## Features

All tools live in the 3D View sidebar (`N`) under the **AtlasMap** tab.

- **Shader to Textures** - Converts unconnected Principled BSDF Base Color, Metallic and Roughness inputs into solid-color image textures (`_A`, `_M`, `_R`/`_S`) and wires them back into the shader. Already-connected inputs are left alone.
- **Roughness / Smoothness** - Generates or converts between roughness and inverted smoothness maps, inserting or removing the Invert node as needed.
- **Pack / Unpack MOS** - Packs Metallic, AO and Roughness/Smoothness into the R, G and B channels of one `_MOS` texture, and unpacks it back into separate maps. A newly baked AO can be added to an existing MOS texture.
- **Ambient Occlusion Baking** - Creates a second UV map (UV1) with Smart UV Project, bakes AO onto it with Cycles, and multiplies the result with the albedo. AO samples UV1 while the other maps keep using the primary UV map.
- **Scale Textures** - Lists the active material's textures and resamples them with Nearest, Bilinear, Bicubic, Lanczos or Auto (Lanczos for downsampling, Bicubic for upsampling).
- **Generate Atlasmap** - Converts every material on the active mesh to textures, packs them into one atlas per map type, remaps the UVs and replaces all material slots with a single atlas material.
- **Generate Shared Atlasmap** - Does the same for all selected meshes at once: every object's materials go into one shared atlas and material, while the objects stay separate. Each object ends up with a single material slot.

## Implementation Highlights

- **NumPy image pipeline** - Pixels are read and written in bulk with `foreach_get`/`foreach_set` and processed as `(height, width, 4)` float arrays. Channel packing, inversion and atlas composition are array slicing operations rather than per-pixel Python loops.
- **Custom separable resampler** - Resampling is implemented from scratch: each axis is resampled separately using Bilinear, Bicubic (Keys, a = -0.5) or Lanczos-3 kernels, with normalized weights and edge clamping.
- **Atlas packing** - Materials are sorted largest first and placed at the first free candidate corner (top-to-bottom, left-to-right), tracked with a boolean occupancy grid that also reserves the margin. The atlas starts at the smallest power-of-two size that fits the total area and grows one side at a time (512x256, 512x512, ...) up to a user-defined maximum. Pixels are only written once a valid layout is found.
- **UV remapping** - Each face's UVs are transformed into its material's atlas region, accounting for Blender's bottom-left image origin. Tiled UVs outside 0-1 are detected and reported as a warning.
- **Node graph handling** - Operators discover existing textures by walking links upstream from Principled BSDF sockets, tag the nodes they create with custom properties so repeated runs reuse them instead of duplicating, and keep the operators composable (atlas generation reuses Unpack MOS, Shader to Textures and the Roughness/Smoothness switch).
- **Automatic node layout** - After every change the material graph is rearranged into columns by depth from the output node, with each node aligned to the socket it feeds and stacked without overlap.
- **State preservation** - AO baking temporarily changes render engine, samples, bake margin, selection, active nodes and links, and restores all of them in a `finally` block, even if the bake fails. All operators support undo.

## Project Structure

- `__init__.py` - extension entry point and registration order.
- `operators/` - Blender operators for texture generation, channel packing, resampling, UV unwrapping, AO baking and atlas generation.
- `ui/` - sidebar panels and the material texture list.
- `utils/` - resampling, atlas layout and composition, material-graph and node-layout helpers.
- `properties.py` - Scene property declarations and cleanup.

## Building and Installing

Build the extension ZIP with Blender's command line (output directory outside the source folder):

```powershell
blender --command extension build --source-dir . --output-dir ..\atlasmap_dist
```

Then install it in Blender via **Edit > Preferences > Get Extensions > Install from Disk**.
