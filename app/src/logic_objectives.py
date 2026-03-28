from app.models import (
    db, WinRequirement, Pile, Character, AttribValue, GENERAL_ID)

def validate_requirements(game_token):
    """
    Evaluates all win requirements for a session.
    Returns: (list of enriched requirements, bool all_met)
    """
    reqs = WinRequirement.query.filter_by(game_token=game_token).all()
    if not reqs:
        return [], False

    all_met = True
    enriched_reqs = []

    for r in reqs:
        is_fulfilled = False
        desc = ""

        # Condition 1: Item in General Storage (Universal)
        if r.item_id and not r.char_id and not r.loc_id:
            pile = Pile.query.filter_by(
                game_token=game_token, item_id=r.item_id, owner_id=GENERAL_ID
            ).first()
            current_qty = pile.quantity if pile else 0
            is_fulfilled = current_qty >= r.quantity
            desc = f"Collect {r.quantity} {r.item.name} in General Storage"

        # Condition 2: Item at a specific Location
        elif r.item_id and r.loc_id and not r.char_id:
            # Sum all piles of this item at the location (across all grid positions)
            piles = Pile.query.filter_by(
                game_token=game_token, item_id=r.item_id, owner_id=r.loc_id
            ).all()
            current_qty = sum(p.quantity for p in piles)
            is_fulfilled = current_qty >= r.quantity
            desc = f"Place {r.quantity} {r.item.name} at {r.location.name}"

        # Condition 3: Item owned by a Character
        elif r.item_id and r.char_id:
            pile = Pile.query.filter_by(
                game_token=game_token, item_id=r.item_id, owner_id=r.char_id
            ).first()
            current_qty = pile.quantity if pile else 0
            is_fulfilled = current_qty >= r.quantity
            desc = f"{r.character.name} must carry {r.quantity} {r.item.name}"

        # Condition 4: Character at a Location
        elif r.char_id and r.loc_id and not r.item_id:
            char = Character.query.get((game_token, r.char_id))
            is_fulfilled = char.location_id == r.loc_id
            desc = f"{char.name} must be at {r.location.name}"

        # Condition 5: Character Attribute Level
        elif r.char_id and r.attrib_id:
            val_rec = AttribValue.query.filter_by(
                game_token=game_token, owner_id=r.char_id, attrib_id=r.attrib_id
            ).first()
            current_val = val_rec.value if val_rec else 0
            is_fulfilled = current_val >= r.attrib_value
            desc = f"{r.character.name} needs {r.attrib.name} ≥ {r.attrib_value}"

        if not is_fulfilled:
            all_met = False
        
        enriched_reqs.append({
            'description': desc,
            'fulfilled': is_fulfilled
        })

    return enriched_reqs, all_met
