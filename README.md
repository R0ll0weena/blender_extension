# Hello Extension

A Blender 4.2+ extension that converts an active material's Principled BSDF Base Color, Metallic, and Roughness values into packed, solid-color image textures, and adds a neutral normal map. Generated textures use `_A` for albedo, `_M` for metallic, `_R` for roughness, `_N` for normal, or `_S` for smoothness.

## Install in Blender

1. In Blender, open **Edit > Preferences > Get Extensions**.
2. Choose **Install from Disk** and select this extension's ZIP archive.
3. Enable **Hello Extension** if Blender does not enable it automatically.
4. In the 3D View, open the sidebar with `N` and choose the **AtlasMap** tab.
5. Expand **Generate Textures** to set options and click **Convert Shader to Textures**. Enable **Smoothness** to generate an inverted smoothness map instead of roughness. Enable **Channel Pack** to put metallic in the red channel and roughness or smoothness in the blue channel of one `_MOS` texture. The generated images are packed into the current `.blend` file.
6. Expand **Normalize Textures** to view the active material's image textures in a scrollable list, with **Name** and **Size** columns. **Ambient Occlusion Baking** is a separate collapsible area.

## Build a ZIP

Run these commands from this project directory. Keep the output directory outside the extension source:

```powershell
New-Item -ItemType Directory -Force ..\hello_extension_dist | Out-Null
blender --command extension build --source-dir . --output-dir ..\hello_extension_dist
```

Install the generated ZIP from `../hello_extension_dist` using **Install from Disk**. Blender must be available on `PATH` for the command above; otherwise, replace `blender` with the path to your Blender executable.

Before publishing, replace the `maintainer` value in `blender_manifest.toml` and update the extension ID, name, and other metadata as needed.

Connected Principled BSDF inputs are left unchanged; textures are generated only for unconnected inputs. The neutral normal map is decoded through a tangent-space Normal Map node.
