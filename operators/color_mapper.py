"""Atlas to simple materials operator."""

import bpy
import numpy as np

from ..utils.atlas_images import image_to_array
from ..utils.node_layout import arrange_material_nodes


def _srgb_to_linear(rgb):
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


class _Sampler:
    """Resolves Principled BSDF inputs to per-face RGBA values by sampling textures at face UV centroids."""

    def __init__(self, uvs):
        self.uvs = uvs
        self.images = {}

    def _image_pixels(self, image):
        if image not in self.images:
            if 0 in image.size[:]:
                raise ValueError(f"Image has no pixel data: {image.name}.")
            pixels = image_to_array(image)
            if not image.is_float and image.colorspace_settings.name == "sRGB":
                pixels[..., :3] = _srgb_to_linear(pixels[..., :3])
            self.images[image] = pixels
        return self.images[image]

    def _sample(self, image):
        pixels = self._image_pixels(image)
        height, width, _ = pixels.shape
        x = np.clip((self.uvs[:, 0] * width).astype(int), 0, width - 1)
        y = np.clip((self.uvs[:, 1] * height).astype(int), 0, height - 1)
        return pixels[y, x]

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


class ATLASMAP_OT_atlas_to_simple_materials(bpy.types.Operator):
    bl_idname = "atlasmap.atlas_to_simple_materials"
    bl_label = "Atlas to Simple Materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "The active object must be a mesh.")
            return {"CANCELLED"}
        mesh = obj.data
        if not mesh.uv_layers:
            self.report({"ERROR"}, "The active mesh needs a UV map.")
            return {"CANCELLED"}
        if not obj.material_slots:
            self.report({"ERROR"}, "The active mesh has no material slots.")
            return {"CANCELLED"}
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if not mesh.polygons:
            self.report({"ERROR"}, "The active mesh has no faces.")
            return {"CANCELLED"}

        uv_layer = next((layer for layer in mesh.uv_layers if layer.active_render), mesh.uv_layers[0])
        centroids = _face_uv_centroids(mesh, uv_layer)
        material_indices = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("material_index", material_indices)
        material_indices = np.clip(material_indices, 0, len(obj.material_slots) - 1)

        # Per face: RGBA, metallic, roughness.
        values = np.zeros((len(mesh.polygons), 6), dtype=np.float32)
        try:
            for slot_index, slot in enumerate(obj.material_slots):
                faces = np.flatnonzero(material_indices == slot_index)
                if not len(faces):
                    continue
                material = slot.material
                principled = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None) if material and material.use_nodes else None
                if principled is None:
                    raise ValueError(f"Material slot {slot_index} has no material with a Principled BSDF node.")
                sampler = _Sampler(centroids[faces])
                values[faces, :4] = sampler.resolve_input(principled.inputs["Base Color"])
                values[faces, 4] = sampler.resolve_input(principled.inputs["Metallic"])[:, 0]
                values[faces, 5] = sampler.resolve_input(principled.inputs["Roughness"])[:, 0]
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        keys = np.round(np.clip(values, 0.0, 1.0) * 255).astype(np.int32)
        _, first_faces, group_of_face = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        source_names = [slot.material.name for slot in obj.material_slots]
        counters = {}
        materials = []
        for face in first_faces:
            source_name = source_names[material_indices[face]]
            counters[source_name] = counters.get(source_name, 0) + 1
            face_values = values[face]
            materials.append(_build_simple_material(f"{source_name}_{counters[source_name]:02d}", face_values[:4], face_values[4], face_values[5]))

        mesh.materials.clear()
        for material in materials:
            mesh.materials.append(material)
        mesh.polygons.foreach_set("material_index", group_of_face.ravel().astype(np.int32))
        mesh.update()

        self.report({"INFO"}, f"Created {len(materials)} materials from {len(mesh.polygons)} faces.")
        return {"FINISHED"}
