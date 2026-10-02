"""Texture inventory UIList."""

import bpy


class ATLASMAP_UL_material_textures(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.type == "TEX_IMAGE" and item.image is not None:
            columns = layout.split(factor=0.7, align=True)
            name = columns.row(align=True)
            name.prop(item, "atlasmap_resample_selected", text="")
            name.label(text=item.image.name, icon="IMAGE_DATA")
            columns.label(text=f"{item.image.size[0]} x {item.image.size[1]}")

    def filter_items(self, context, data, property_name):
        nodes = getattr(data, property_name)
        flags = [self.bitflag_filter_item if node.type == "TEX_IMAGE" and node.image is not None else 0 for node in nodes]
        return flags, []
