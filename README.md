# Hello Extension

A Blender 4.2+ extension that converts an active material's Principled BSDF Base Color, Metallic, and Roughness values into packed, solid-color image textures.

## Install in Blender

1. In Blender, open **Edit > Preferences > Get Extensions**.
2. Choose **Install from Disk** and select this extension's ZIP archive.
3. Enable **Hello Extension** if Blender does not enable it automatically.
4. In the 3D View, open the sidebar with `N` and choose the **AtlasMap** tab.
5. Choose the texture width and height, then click **Convert Shader to Textures**. The generated image texture nodes are connected to the Principled BSDF inputs, and the images are packed into the current `.blend` file.

## Build a ZIP

Run these commands from this project directory. Keep the output directory outside the extension source:

```powershell
New-Item -ItemType Directory -Force ..\hello_extension_dist | Out-Null
blender --command extension build --source-dir . --output-dir ..\hello_extension_dist
```

Install the generated ZIP from `../hello_extension_dist` using **Install from Disk**. Blender must be available on `PATH` for the command above; otherwise, replace `blender` with the path to your Blender executable.

Before publishing, replace the `maintainer` value in `blender_manifest.toml` and update the extension ID, name, and other metadata as needed.

The operator expects the three Principled BSDF inputs to contain unlinked values. It will not replace inputs that are already connected.
