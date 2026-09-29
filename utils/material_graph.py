"""Material-node graph helpers."""

import bpy


def create_albedo_texture(material, principled, texture_size):
    color = tuple(principled.inputs["Base Color"].default_value)
    image = bpy.data.images.new(f"{material.name}_A", width=texture_size, height=texture_size, alpha=True, float_buffer=True)
    image.colorspace_settings.name = "sRGB"
    image.pixels.foreach_set(color * (texture_size * texture_size))
    image.pack()
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    node.label = "Albedo"
    node.location = (principled.location.x - 700, principled.location.y + 80)
    return node


def setup_ao_material_graph(material, principled, ao_image, ao_node, texture_size, uv_layer_name):
    node_tree = material.node_tree
    base_color_input = principled.inputs["Base Color"]
    mix_node = next((node for node in node_tree.nodes if node.get("atlasmap_ao_multiply", False)), None)
    albedo_node = None
    if mix_node is not None and mix_node.inputs["A"].links:
        source = mix_node.inputs["A"].links[0].from_node
        if source.type == "TEX_IMAGE":
            albedo_node = source
    if albedo_node is None:
        upstream_nodes = []
        pending_nodes = [link.from_node for link in base_color_input.links]
        visited = set()
        while pending_nodes:
            node = pending_nodes.pop()
            if node in visited:
                continue
            visited.add(node)
            if node.type == "TEX_IMAGE" and node.image is not None:
                if not node.get("atlasmap_ao_target", False) and node.image != ao_image:
                    upstream_nodes.append(node)
                continue
            pending_nodes.extend(link.from_node for socket in node.inputs for link in socket.links)
        albedo_node = next((node for node in upstream_nodes if node.label == "Albedo" or node.image.name.endswith("_A")), upstream_nodes[0] if upstream_nodes else None)
    if albedo_node is None:
        albedo_node = next((node for node in node_tree.nodes if node.type == "TEX_IMAGE" and node.image is not None and (node.label == "Albedo" or node.image.name.endswith("_A"))), None)
    if albedo_node is None:
        albedo_node = create_albedo_texture(material, principled, texture_size)
    albedo_node.location = (principled.location.x - 700, principled.location.y + 80)
    ao_node.label = "Ambient Occlusion"
    ao_node.location = (albedo_node.location.x, albedo_node.location.y - 320)
    uv_node = next((node for node in node_tree.nodes if node.get("atlasmap_ao_uv_map", False)), None)
    if uv_node is None:
        uv_node = node_tree.nodes.new("ShaderNodeUVMap")
        uv_node["atlasmap_ao_uv_map"] = True
    uv_node.uv_map = uv_layer_name
    uv_node.location = (ao_node.location.x - 260, ao_node.location.y)
    if mix_node is None:
        mix_node = node_tree.nodes.new("ShaderNodeMix")
        mix_node["atlasmap_ao_multiply"] = True
    mix_node.data_type = "RGBA"
    mix_node.blend_type = "MULTIPLY"
    mix_node.inputs["Factor"].default_value = 1.0
    mix_node.location = (principled.location.x - 300, principled.location.y + 80)
    for socket in (mix_node.inputs["A"], mix_node.inputs["B"]):
        for link in list(socket.links):
            node_tree.links.remove(link)
    for link in list(base_color_input.links):
        node_tree.links.remove(link)
    node_tree.links.new(albedo_node.outputs["Color"], mix_node.inputs["A"])
    node_tree.links.new(ao_node.outputs["Color"], mix_node.inputs["B"])
    node_tree.links.new(mix_node.outputs["Result"], base_color_input)
    for link in list(ao_node.inputs["Vector"].links):
        node_tree.links.remove(link)
    node_tree.links.new(uv_node.outputs["UV"], ao_node.inputs["Vector"])
