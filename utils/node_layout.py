"""Material node-graph auto-layout."""

import bpy

COLUMN_GAP = 80
ROW_GAP = 40
SOCKET_HEIGHT = 22
HEADER_HEIGHT = 40
# Estimated extra height for node widgets (dropdowns, image selectors) when Blender has not drawn the node yet.
EXTRA_HEIGHT = {"TEX_IMAGE": 170, "MIX": 60, "SEPARATE_COLOR": 30, "NORMAL_MAP": 60, "UVMAP": 40, "INVERT": 0}


def _visible(sockets):
    return [socket for socket in sockets if socket.enabled and not socket.hide]


def _node_height(node):
    if node.dimensions.y > 0:
        return node.dimensions.y / bpy.context.preferences.system.ui_scale
    socket_count = len(_visible(node.inputs)) + len(_visible(node.outputs))
    return HEADER_HEIGHT + SOCKET_HEIGHT * socket_count + EXTRA_HEIGHT.get(node.type, 0)


def _socket_offset(socket):
    node = socket.node
    return HEADER_HEIGHT + SOCKET_HEIGHT * (len(_visible(node.outputs)) + _visible(node.inputs).index(socket))


def arrange_material_nodes(material):
    """Lay out nodes feeding the material output in columns right-to-left, stacked without overlap.

    Each node sits one column left of its furthest-right consumer and is aligned with the input socket it feeds.
    Nodes not connected to the output are placed in a row below the arranged graph. Framed nodes are left untouched.
    """
    node_tree = material.node_tree
    nodes = [node for node in node_tree.nodes if node.type != "FRAME" and node.parent is None]
    root = next((node for node in nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    root = root or next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    if root is None:
        return

    allowed = set(nodes)
    depth = {root: 0}
    pending = [root]
    while pending:
        node = pending.pop()
        for socket in node.inputs:
            for link in socket.links:
                source = link.from_node
                if source in allowed and depth.get(source, -1) < depth[node] + 1 <= len(nodes):
                    depth[source] = depth[node] + 1
                    pending.append(source)

    columns = {}
    for node, column in depth.items():
        columns.setdefault(column, []).append(node)

    column_x = root.location.x
    lowest = root.location.y - _node_height(root)
    for column in range(1, max(columns) + 1):
        column_nodes = columns.get(column, [])
        if not column_nodes:
            continue
        column_x -= max(node.width for node in column_nodes) + COLUMN_GAP

        def desired_y(node):
            targets = [link.to_node.location.y - _socket_offset(link.to_socket) + HEADER_HEIGHT
                       for socket in node.outputs for link in socket.links
                       if link.to_node in depth and depth[link.to_node] < column and link.to_socket.enabled and not link.to_socket.hide]
            return max(targets) if targets else root.location.y

        previous_bottom = None
        for node in sorted(column_nodes, key=desired_y, reverse=True):
            y = desired_y(node)
            if previous_bottom is not None:
                y = min(y, previous_bottom - ROW_GAP)
            node.location = (column_x, y)
            previous_bottom = y - _node_height(node)
            lowest = min(lowest, previous_bottom)

    x = column_x
    row_y = lowest - ROW_GAP * 2
    for node in nodes:
        if node not in depth:
            node.location = (x, row_y)
            x += node.width + COLUMN_GAP
