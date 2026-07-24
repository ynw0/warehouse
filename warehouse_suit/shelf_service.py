# -*- coding: utf-8 -*-
"""Shelf layer and zone services."""

import json


def validate_zones(layers):
    parsed_layers = []
    for layer_index, layer in enumerate(layers, start=1):
        zones = []
        for zone_index, zone in enumerate(layer.get("zones", []), start=1):
            name = str(zone.get("name") or chr(64 + zone_index)).strip().upper()
            zones.append(
                {
                    "name": name,
                    "note": str(zone.get("note") or "").strip(),
                    "capacity": int(zone.get("capacity") or 10),
                    "color": zone.get("color") or "#69a7ff",
                }
            )
        if not zones:
            zones.append({"name": "A", "note": "", "capacity": 10, "color": "#69a7ff"})
        parsed_layers.append({"layer_number": int(layer.get("layer_number") or layer_index), "zones": zones})
    if not parsed_layers:
        parsed_layers.append({"layer_number": 1, "zones": [{"name": "A", "note": "", "capacity": 10, "color": "#69a7ff"}]})
    return parsed_layers


def replace_layers(cursor, shelf_id, layers):
    cursor.execute("DELETE FROM shelf_layers WHERE shelf_id = ?", (shelf_id,))
    for index, layer in enumerate(validate_zones(layers), start=1):
        cursor.execute(
            """
            INSERT INTO shelf_layers (shelf_id, layer_number, zones)
            VALUES (?, ?, ?)
            """,
            (
                shelf_id,
                layer.get("layer_number") or index,
                json.dumps(layer["zones"], ensure_ascii=False),
            ),
        )
