# Hello Extension

A minimal Blender 4.2+ extension starter. The extension adds an **AtlasMap** tab to the 3D View sidebar (`N` panel) with a button that reports a greeting.

## Install in Blender

1. In Blender, open **Edit > Preferences > Get Extensions**.
2. Choose **Install from Disk** and select this extension's ZIP archive.
3. Enable **Hello Extension** if Blender does not enable it automatically.
4. In the 3D View, open the sidebar with `N` and choose the **AtlasMap** tab.

## Build a ZIP

Run these commands from this project directory. Keep the output directory outside the extension source:

```powershell
New-Item -ItemType Directory -Force ..\hello_extension_dist | Out-Null
blender --command extension build --source-dir . --output-dir ..\hello_extension_dist
```

Install the generated ZIP from `../hello_extension_dist` using **Install from Disk**. Blender must be available on `PATH` for the command above; otherwise, replace `blender` with the path to your Blender executable.

Before publishing, replace the `maintainer` value in `blender_manifest.toml` and update the extension ID, name, and other metadata as needed.

## Customize

- Change the operator behavior in `__init__.py`.
- Add classes to `_CLASSES`; keep registration in declaration order and unregistration in reverse order.
- Keep extension metadata in `blender_manifest.toml`.
