# Hello Extension

A Blender 4.2+ extension that converts an active material's Principled BSDF Base Color, Metallic, and Roughness values into packed, solid-color image textures, and adds a neutral normal map. Generated textures use `_A` for albedo, `_M` for metallic, `_R` for roughness, `_N` for normal, or `_S` for smoothness.

## Install in Blender

1. In Blender, open **Edit > Preferences > Get Extensions**.
2. Choose **Install from Disk** and select this extension's ZIP archive.
3. Enable **Hello Extension** if Blender does not enable it automatically.
4. In the 3D View, open the sidebar with `N` and choose the **AtlasMap** tab.
5. Expand **Generate Textures** to set options and click **Convert Shader to Textures**. Enable **Smoothness** to generate an inverted smoothness map instead of roughness. Enable **Channel Pack** to put metallic in the red channel and roughness or smoothness in the blue channel of one `_MOS` texture. The generated images are packed into the current `.blend` file.
6. Expand **Generate AtlasMap** to set the atlas size limit, margin, and background color. **Combine Materials** is a placeholder button; atlas generation is not implemented yet.
7. Expand **Scale Textures** to view the active material's image textures in a scrollable list, with **Name** and **Size** columns. Select a texture, choose a rescale factor and method, then click **Resample Texture**. **Auto** (the default) uses Lanczos when downsampling and Bicubic when upsampling. **Generate Textures**, **Generate AtlasMap**, **Scale Textures**, and **Ambient Occlusion Baking** are sibling collapsible panels in the AtlasMap tab.
8. In **Ambient Occlusion Baking**, expand the inline **UV Unwrap Settings** area at the top to adjust Smart UV Project options. The **Unwrap UV1** button is directly below the collapsed area and creates or overwrites the active mesh's second UV layer; the first UV layer is preserved.
9. Enable **Re-unwrap UV1** to run Smart UV Project automatically before baking; it is enabled by default. When disabled, UV1 must already exist. Set the AO texture size, Cycles samples, and island margin, then click **Bake Ambient Occlusion**. Blender's current bake margin type and clear-image settings are respected. AO images are baked using UV1 and saved in the `.blend` as `<object>_AO`; after baking, the AO image is multiplied with the material's albedo through a Mix Color node. If no albedo texture is present, an albedo texture is generated at the **Generate Textures** pane's texture size from the Principled BSDF Base Color.

## Project Structure

- `__init__.py` - extension entry point and registration order.
- `operators/` - Blender operators for conversion, resampling, UV unwrapping, AO baking, and atlas generation.
- `ui/` - sidebar panels and the material texture list.
- `utils/` - image resampling and material-node graph helpers.
- `properties.py` - Scene property declarations and cleanup.

## Build a ZIP

Run these commands from this project directory. Keep the output directory outside the extension source:

```powershell
New-Item -ItemType Directory -Force ..\hello_extension_dist | Out-Null
blender --command extension build --source-dir . --output-dir ..\hello_extension_dist
```

Install the generated ZIP from `../hello_extension_dist` using **Install from Disk**. Blender must be available on `PATH` for the command above; otherwise, replace `blender` with the path to your Blender executable.

Before publishing, replace the `maintainer` value in `blender_manifest.toml` and update the extension ID, name, and other metadata as needed.

Connected Principled BSDF inputs are left unchanged; textures are generated only for unconnected inputs. The neutral normal map is decoded through a tangent-space Normal Map node.
