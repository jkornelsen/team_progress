import json
import os
from pathlib import Path

# Setup directories
OLD_DIR = "../history/pre_orm/app/data_files"
NEW_DIR = "../app/data_files"

def migrate_file(filename):
    old_path = os.path.join(OLD_DIR, filename)
    #new_filename = filename.replace(".json", "_new.json")
    new_filename = filename
    new_path = os.path.join(NEW_DIR, new_filename)

    with open(old_path, 'r') as f:
        old_data = json.load(f)

    # 1. Initialize Global ID Mapping
    # format: { 'type': { old_id: new_id } }
    id_map = {
        "attribs": {},
        "items": {},
        "locations": {},
        "characters": {},
        "events": {}
    }
    
    global_counter = 2  # leave GENERAL_ID 1 free
    
    # 2. First Pass: Assign New Unique IDs
    for category in id_map.keys():
        if category in old_data:
            for entity in old_data[category]:
                id_map[category][entity['id']] = global_counter
                global_counter += 1

    # Storage type mapping helper
    storage_map = {
        "universal": "u",
        "carried": "c",
        "local": "l"
    }

    # 3. Second Pass: Reconstruct Data
    new_entities = {
        "items": [],
        "locations": [],
        "characters": [],
        "attribs": [],
        "events": []
    }
    general_piles = []

    # Process Attributes
    for a in old_data.get("attribs", []):
        new_entities["attribs"].append({
            "id": id_map["attribs"][a["id"]],
            "name": a["name"],
            "description": a["description"],
            "abilities": [], 
            "is_binary": a.get("is_binary", False),
            "enum_list": a.get("enum_list", [])
        })

    # Process Items
    for i in old_data.get("items", []):
        if i.get("storage_type") == "universal":
            general_piles.append({
                "item_id": id_map["items"][i["id"]],
                "quantity": i.get("quantity", 0)
            })

        migrated_item = {
            "id": id_map["items"][i["id"]],
            "name": i["name"],
            "description": i["description"],
            "attribs": [],
            "abilities": [],
            "storage_type": storage_map.get(i.get("storage_type"), "u"),
            "q_limit": i.get("q_limit", 0.0),
            "toplevel": i.get("toplevel", False),
            "masked": i.get("masked", False),
            "recipes": []
        }
        # Migrate Recipes and their internal IDs/Refs
        for r in i.get("recipes", []):
            new_recipe = {
                "id": r["id"], 
                "rate_amount": r.get("rate_amount", 1.0),
                "rate_duration": r.get("rate_duration", 3.0),
                "instant": r.get("instant", False),
                "sources": [],
                "byproducts": [], # New field for multi-output
                "attrib_reqs": []
            }

            # Check for byproducts in the old data
            for bp in r.get("byproducts", []):
                # Use .get("item_id") to read from the OLD data
                # but assign it to "product_id" for the NEW data
                old_bp_id = bp.get("item_id") 
                new_recipe["byproducts"].append({
                    "product_id": id_map["items"].get(old_bp_id, old_bp_id),
                    "rate_amount": bp.get("rate_amount", 1.0)
                })

            # 2. Map the Source Items
            for src in r.get("sources", []):
                old_src_id = src.get("item_id")
                new_src_id = id_map["items"].get(old_src_id, old_src_id)
                
                new_recipe["sources"].append({
                    "item_id": new_src_id,
                    "q_required": src.get("q_required", 1.0),
                    "preserve": src.get("preserve", False)
                })

            # 3. Map the Attribute Requirements
            for req in r.get("attrib_reqs", []):
                old_attr_id = req.get("attrib_id")
                new_attr_id = id_map["attribs"].get(old_attr_id, old_attr_id)
                
                new_recipe["attrib_reqs"].append({
                    "attrib_id": new_attr_id,
                    "value_range": req.get("value_range", [0.0, 0.0])
                })

            migrated_item["recipes"].append(new_recipe)
        new_entities["items"].append(migrated_item)

    # Process Locations
    for l in old_data.get("locations", []):
        # Scrub dimensions: [0,0] -> None
        raw_dims = l.get("dimensions")
        new_dims = raw_dims if raw_dims and any(d > 0 for d in raw_dims) else None
        
        # Scrub excluded: [0,0,0,0] -> None
        raw_excl = l.get("excluded")
        new_excl = raw_excl if raw_excl and any(e > 0 for e in raw_excl) else None

        new_loc = {
            "id": id_map["locations"][l["id"]],
            "name": l["name"],
            "description": l["description"],
            "attribs": [],
            "abilities": [],
            "dimensions": new_dims,
            "excluded": new_excl,
            "toplevel": l.get("toplevel", False),
            "masked": l.get("masked", False),
            "items": [],
            "item_refs": [],
            "destinations": []
        }
        for item in l.get("items", []):
            old_pos = item.get("position")
            if old_pos == [0, 0] or not old_pos:
                new_pos = None
            else:
                new_pos = old_pos
            new_loc["items"].append({
                "item_id": id_map["items"].get(item["item_id"], item["item_id"]),
                "quantity": item["quantity"],
                "position": new_pos
            })
        for dest in l.get("destinations", []):
            new_loc["destinations"].append({
                "loc2_id": id_map["locations"].get(dest["loc2_id"], dest["loc2_id"]),
                "duration": dest["duration"],
                "bidirectional": dest.get("bidirectional", False)
            })
        new_entities["locations"].append(new_loc)

    # Process Characters
    for c in old_data.get("characters", []):
        old_pos = c.get("position")
        if old_pos == [0, 0] or not old_pos:
            new_pos = None
        else:
            new_pos = old_pos
        new_char = {
            "id": id_map["characters"][c["id"]],
            "name": c["name"],
            "description": c["description"],
            "attribs": [[id_map["attribs"].get(pair[0], pair[0]), pair[1]] for pair in c.get("attribs", [])],
            "abilities": [],
            "toplevel": c.get("toplevel", False),
            "location_id": id_map["locations"].get(c["location_id"], c["location_id"]),
            "position": new_pos,
            "travel_party": "",
            "items": []
        }
        for item in c.get("items", []):
            new_char["items"].append({
                "item_id": id_map["items"].get(item["item_id"], item["item_id"]),
                "quantity": item["quantity"],
                "slot": item.get("slot", "")
            })
        new_entities["characters"].append(new_char)

    # Final Assembly
    new_structure = {
        "entities": new_entities,
        "general data": {
            "piles": general_piles,
            "progress": []
        },
        "overall settings": {
            "title": old_data["overall"].get("title", "Migrated Adventure"),
            "description": old_data["overall"].get("description", ""),
            "number_format": old_data["overall"].get("number_format", "en_US"),
            "slots": old_data["overall"].get("slots", []),
            "progress_type": old_data["overall"].get("progress_type", "?"),
            "multiplayer": old_data["overall"].get("multiplayer", False)
        }
    }

    with open(new_path, 'w') as f:
        json.dump(new_structure, f, indent=4)
    print(f"Migrated: {filename} -> {new_filename}")

# Run for the specific file mentioned
if __name__ == "__main__":
    if not os.path.exists(NEW_DIR):
        os.makedirs(NEW_DIR)
    
    # Run specific file
    #migrate_file("01_Bacon_for_Dinner.json")
    
    # Run for directory
    for f in os.listdir(OLD_DIR):
        if f.endswith(".json") and f != "00_Default.json":
            migrate_file(f)
