# Claude Code Project Rules

## AI Behavior & Scope
- **Strict Scope**: Execute ONLY the explicit task requested. Do not refactor unrelated code, add "nice-to-have" features, or optimize surrounding logic unless explicitly ordered.
- **No Side Effects**: Never modify files outside the direct scope of the prompt.
- **Conciseness**: Write minimal, functional code. Skip long conversational explanations before or after code generation. Just deliver the solution.
- **Chat Output**: Keep chat responses short, to the point and concise. Skip preamble, recaps and filler; give only what's needed.

## Development Environment
- This is a Blender addon (Blender 4.2+ extension, see `blender_manifest.toml`).
- Focus entirely on the specific implementation patterns for this framework.

## Project
AtlasMap: bakes Principled BSDF values to textures, resamples textures, bakes AO on UV1, and (WIP) combines materials into an atlas. UI lives in the 3D View sidebar's **AtlasMap** tab.

- `__init__.py` - registration entry point
- `operators/` - Blender operators
- `ui/` - panels and UI lists
- `utils/` - resampling, material-graph and atlas helpers
- `properties.py` - Scene properties
