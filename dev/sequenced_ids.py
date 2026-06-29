#-------------------------------------------------------------------------------
# Show all sequenced IDs in a JSON file. 
#-------------------------------------------------------------------------------
import json
import sys

def list_entities(file_path):
    # Load the JSON data
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Get the entities object
    entities_data = data.get("entities", {})
    
    # Entity types to look for
    entity_types = ["items", "locations", "characters", "attribs", "events"]
    
    all_entities = []
    all_ids = set()
    
    # Extract entities from each category
    for etype in entity_types:
        category_list = entities_data.get(etype, [])
        for entity in category_list:
            entity_id = entity.get("id")
            entity_name = entity.get("name")
            
            if entity_id is not None:
                if entity_id in all_ids:
                    duplicate = True
                else:
                    duplicate = False
                    all_ids.add(entity_id)
                all_entities.append({
                    "id": entity_id,
                    "dup": duplicate,
                    "name": entity_name,
                    "type": etype[:-1]  # Singular version of the category type (e.g., 'item')
                })

            if etype == "items" and "recipes" in entity:
                for recipe in entity.get("recipes", []):
                    recipe_id = recipe.get("id")
                    if recipe_id is not None:
                        if recipe_id in all_ids:
                            duplicate = True
                        else:
                            duplicate = False
                            all_ids.add(recipe_id)
                        all_entities.append({
                            "id": recipe_id,
                            "dup": duplicate,
                            "name": entity_name,  # Uses the parent item's name
                            "type": "recipe"
                        })
    
    # Sort all entities by ID
    all_entities.sort(key=lambda x: x["id"])
    
    # Display the sorted list
    print(f"{'ID':<6} | {'Type':<10} | {'Name'}")
    print("-" * 50)
    for ent in all_entities:
        name_str = ent['name'] if ent['name'] is not None else "N/A"
        dup_str = " !!" if ent['dup'] else "   "
        print(f"{ent['id']:<6}{dup_str} | {ent['type']:<10} | {name_str}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        filename = sys.argv[1]
        list_entities(filename)
    else:
        print("Usage: python entity_ids.py <filename>")

