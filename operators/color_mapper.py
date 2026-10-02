"""Atlas to simple materials operator."""

import bpy
import numpy as np

from ..utils.atlas_images import image_to_array
from ..utils.color_names import color_name
from ..utils.node_layout import arrange_material_nodes


def _srgb_to_linear(rgb):
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb):
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)


class _Sampler:
    """Resolves Principled BSDF inputs to per-face RGBA values by sampling textures at face UV centroids."""

    def __init__(self, uvs, images):
        self.uvs = uvs
        # Shared across the whole run so each image is read from Blender only once.
        self.images = images

    def _image_pixels(self, image):
        """Return (raw pixels, needs sRGB to linear); conversion is applied to samples only."""
        if image not in self.images:
            if 0 in image.size[:]:
                raise ValueError(f"Image has no pixel data: {image.name}.")
            self.images[image] = image_to_array(image), not image.is_float and image.colorspace_settings.name == "sRGB"
        return self.images[image]

    def _sample(self, image):
        pixels, is_srgb = self._image_pixels(image)
        height, width, _ = pixels.shape
        x = np.clip((self.uvs[:, 0] * width).astype(int), 0, width - 1)
        y = np.clip((self.uvs[:, 1] * height).astype(int), 0, height - 1)
        samples = pixels[y, x]
        if is_srgb:
            samples[:, :3] = _srgb_to_linear(samples[:, :3])
        return samples

    def _broadcast(self, value):
        result = np.ones((len(self.uvs), 4), dtype=np.float32)
        result[:, :3] = np.asarray(value, dtype=np.float32).reshape(-1, 1) if np.ndim(value) else value
        return result

    def resolve_input(self, socket):
        if socket.links:
            return self.resolve_output(socket.links[0].from_socket)
        value = socket.default_value
        if hasattr(value, "__len__"):
            result = np.ones((len(self.uvs), 4), dtype=np.float32)
            result[:, :len(value)] = tuple(value)
            return result
        return self._broadcast(value)

    def resolve_output(self, socket):
        node = socket.node
        if node.type == "REROUTE":
            return self.resolve_input(node.inputs[0])
        if node.type == "TEX_IMAGE" and node.image is not None:
            pixels = self._sample(node.image)
            return pixels if socket.name == "Color" else self._broadcast(pixels[:, 3:4].T)
        if node.type == "SEPARATE_COLOR" and node.mode == "RGB":
            channel = ("Red", "Green", "Blue").index(socket.name)
            return self._broadcast(self.resolve_input(node.inputs["Color"])[:, channel:channel + 1].T)
        if node.type == "INVERT":
            fac = self.resolve_input(node.inputs["Fac"])[:, :1]
            color = self.resolve_input(node.inputs["Color"])
            color[:, :3] = color[:, :3] + fac * (1.0 - 2.0 * color[:, :3])
            return color
        if node.type == "MIX" and node.data_type == "RGBA" and node.blend_type == "MULTIPLY":
            # AO multiply: keep the albedo, drop the AO.
            return self.resolve_input(next(socket for socket in node.inputs if socket.identifier == "A_Color"))
        raise ValueError(f"Unsupported node '{node.name}' feeding the Principled BSDF.")


def _face_uv_centroids(mesh, uv_layer):
    uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    uv_layer.data.foreach_get("uv", uvs)
    uvs = uvs.reshape(-1, 2)
    loop_start = np.empty(len(mesh.polygons), dtype=np.int32)
    loop_total = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_start)
    mesh.polygons.foreach_get("loop_total", loop_total)
    order = np.argsort(loop_start)
    sums = np.empty((len(loop_start), 2), dtype=np.float32)
    sums[order] = np.add.reduceat(uvs, loop_start[order], axis=0)
    return (sums / loop_total[:, None]) % 1.0


def _build_simple_material(name, color, metallic, roughness):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    principled.inputs["Base Color"].default_value = tuple(float(value) for value in color)
    principled.inputs["Metallic"].default_value = float(metallic)
    principled.inputs["Roughness"].default_value = float(roughness)
    arrange_material_nodes(material)
    return material


def _face_values(obj, images):
    """Per face of obj: linear RGBA base color, metallic, roughness as an (N, 6) array; images is the run-wide pixel cache."""
    mesh = obj.data
    if not mesh.uv_layers:
        raise ValueError(f"'{obj.name}' needs a UV map.")
    if not obj.material_slots:
        raise ValueError(f"'{obj.name}' has no material slots.")
    if not mesh.polygons:
        raise ValueError(f"'{obj.name}' has no faces.")

    uv_layer = next((layer for layer in mesh.uv_layers if layer.active_render), mesh.uv_layers[0])
    centroids = _face_uv_centroids(mesh, uv_layer)
    material_indices = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", material_indices)
    material_indices = np.clip(material_indices, 0, len(obj.material_slots) - 1)

    values = np.zeros((len(mesh.polygons), 6), dtype=np.float32)
    for slot_index, slot in enumerate(obj.material_slots):
        faces = np.flatnonzero(material_indices == slot_index)
        if not len(faces):
            continue
        material = slot.material
        principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None) if material and material.use_nodes else None
        if principled is None:
            raise ValueError(f"'{obj.name}' material slot {slot_index} has no material with a Principled BSDF node.")
        sampler = _Sampler(centroids[faces], images)
        values[faces, :4] = sampler.resolve_input(principled.inputs["Base Color"])
        values[faces, 4] = sampler.resolve_input(principled.inputs["Metallic"])[:, 0]
        values[faces, 5] = sampler.resolve_input(principled.inputs["Roughness"])[:, 0]
    return values


class _SimpleMaterialPool:
    """Simple materials created during one run; similar albedo, metallic and roughness reuse an earlier material."""

    def __init__(self, tolerance):
        self.tolerance = tolerance
        self.materials = []
        # Per material: sRGB albedo, metallic, roughness. Capacity doubles so appending stays cheap.
        self.keys = np.empty((64, 5), dtype=np.float32)
        self.name_counts = {}

    def get(self, values):
        key = np.concatenate([_linear_to_srgb(np.clip(values[:3], 0.0, 1.0)), np.clip(values[4:6], 0.0, 1.0)])
        count = len(self.materials)
        if count:
            matches = np.flatnonzero(np.all(np.abs(self.keys[:count] - key) <= self.tolerance, axis=1))
            if len(matches):
                return self.materials[matches[0]]
        name = color_name(key[:3])
        self.name_counts[name] = self.name_counts.get(name, 0) + 1
        if self.name_counts[name] > 1:
            name = f"{name} {self.name_counts[name]}"
        material = _build_simple_material(name, values[:4], values[4], values[5])
        if count == len(self.keys):
            self.keys = np.concatenate([self.keys, np.empty_like(self.keys)])
        self.keys[count] = key
        self.materials.append(material)
        return material


class ATLASMAP_OT_atlas_to_simple_materials(bpy.types.Operator):
    bl_idname = "atlasmap.atlas_to_simple_materials"
    bl_label = "Atlas to Simple Materials"
    bl_description = "Replace the atlas material of every selected mesh with one simple material per color, shared across objects"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        selected = [obj for obj in context.selected_objects if obj.type == "MESH"]
        active = context.active_object
        if active is not None and active.type == "MESH" and active not in selected:
            selected.insert(0, active)
        # Objects sharing a mesh are converted once.
        by_mesh = {}
        for obj in selected:
            by_mesh.setdefault(obj.data, obj)
        targets = list(by_mesh.values())
        if not targets:
            self.report({"ERROR"}, "Select at least one mesh object.")
            return {"CANCELLED"}
        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        pool = _SimpleMaterialPool(context.scene.atlasmap_simple_similarity / 100.0)
        images = {}
        errors = []
        converted = set()
        for obj in targets:
            # Sample everything before touching the mesh so a failure leaves the object unchanged.
            try:
                values = _face_values(obj, images)
            except ValueError as error:
                errors.append(str(error))
                continue
            keys = np.round(np.clip(values, 0.0, 1.0) * 255).astype(np.int32)
            _, first_faces, group_of_face = np.unique(keys, axis=0, return_index=True, return_inverse=True)
            group_materials = [pool.get(values[face]) for face in first_faces]
            slot_materials = list(dict.fromkeys(group_materials))
            slot_of_group = np.array([slot_materials.index(material) for material in group_materials], dtype=np.int32)

            mesh = obj.data
            mesh.materials.clear()
            for material in slot_materials:
                mesh.materials.append(material)
            mesh.polygons.foreach_set("material_index", slot_of_group[group_of_face.ravel()])
            mesh.update()
            converted.add(mesh)

        if not converted:
            self.report({"ERROR"}, " ".join(errors))
            return {"CANCELLED"}
        # Objects sharing a converted mesh count as processed too.
        object_count = sum(1 for obj in selected if obj.data in converted)
        message = f"Created {len(pool.materials)} unique simple materials for {object_count} object(s)."
        if errors:
            self.report({"WARNING"}, f"{message} Skipped: {' '.join(errors)}")
        else:
            self.report({"INFO"}, message)
        if not bpy.app.background:
            def draw(menu, _context):
                menu.layout.label(text=f"Unique materials created: {len(pool.materials)}")
                menu.layout.label(text=f"Objects processed: {object_count}")
                if errors:
                    menu.layout.label(text=f"Objects skipped: {len(errors)} (see Info log)", icon="ERROR")
            context.window_manager.popup_menu(draw, title="Atlas to Simple Materials", icon="INFO")
        return {"FINISHED"}
