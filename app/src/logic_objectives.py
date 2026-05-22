from app.models import (
    db, WinRequirement, Pile, Character, Item, AttribVal, GENERAL_ID)
from app.utils import format_num, maskable_name

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

        # --- CASE 1: ITEM QUANTITY GOALS ---
        if r.item_id and not r.attrib_id:
            q_required = format_num(r.quantity) if r.quantity != 1 else ''

            # A. Item in General Storage (Universal)
            if not r.char_id and not r.loc_id:
                pile = Pile.query.filter_by(
                    game_token=game_token, item_id=r.item_id, owner_id=GENERAL_ID
                ).first()
                current_qty = pile.quantity if pile else 0
                is_fulfilled = current_qty >= r.quantity
                desc = f"🌐 {q_required} {maskable_name(r.item)}"

            # B. Item at a specific Location
            elif r.loc_id:
                piles = Pile.query.filter_by(
                    game_token=game_token, item_id=r.item_id, owner_id=r.loc_id
                ).all()
                current_qty = sum(p.quantity for p in piles)
                is_fulfilled = current_qty >= r.quantity
                desc = f"📍 {q_required} {maskable_name(r.item)} at {maskable_name(r.loc)}"

            # C. Item owned by a Character (Carried)
            elif r.char_id:
                pile = Pile.query.filter_by(
                    game_token=game_token, item_id=r.item_id, owner_id=r.char_id
                ).first()
                current_qty = pile.quantity if pile else 0
                is_fulfilled = current_qty >= r.quantity
                desc = f"👤 {r.char.name} must carry {q_required} {maskable_name(r.item)}"

        # --- CASE 2: LOCATION GOALS (Char at Loc) ---
        elif r.char_id and r.loc_id and not r.item_id and not r.attrib_id:
            char = Character.query.get((game_token, r.char_id))
            is_fulfilled = char.location_id == r.loc_id
            desc = f"👤 {char.name} must be at {maskable_name(r.loc)}"

        # --- CASE 3: ATTRIBUTE GOALS ---
        elif r.attrib_id:
            # Determine the subject (Character or Item)
            subject_id = r.char_id or r.item_id
            subject_name = r.char.name if r.char_id else maskable_name(r.item)
            icon = "👤" if r.char_id else "📦"
            
            val_rec = AttribVal.query.filter_by(
                game_token=game_token, subject_id=subject_id, attrib_id=r.attrib_id
            ).first()
            current_val = val_rec.value if val_rec else 0

            if r.attrib.is_binary:
                is_fulfilled = (current_val == r.attrib_value)
                have = "must have" if r.attrib_value > 0 else "cannot have"
                desc = f"👤 {r.char.name} {have} {r.attrib.name}"
            elif r.attrib.enum_list:
                is_fulfilled = (current_val == r.attrib_value)
                try:
                    state_name = r.attrib.enum_list[int(r.attrib_value)]
                except:
                    state_name = str(r.attrib_value)
                desc = f"👤 {r.char.name} must have {r.attrib.name} set to '{state_name}'"
            else:
                is_fulfilled = current_val >= r.attrib_value
                desc = f"👤 {r.char.name} needs {r.attrib.name} ≥ {format_num(r.attrib_value)}"

        if not is_fulfilled:
            all_met = False
        
        enriched_reqs.append({
            'description': desc,
            'fulfilled': is_fulfilled
        })

    return enriched_reqs, all_met
