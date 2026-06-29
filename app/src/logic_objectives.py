from flask import g
from sqlalchemy import select
from app.models import (
    db, WinRequirement, Pile, Character, Item, AttribVal, GENERAL_ID)
from app.utils import format_num, maskable_name

def validate_requirements(scenario):
    """
    Evaluates all win requirements for a session.
    Returns: (list of enriched requirements, bool all_met)
    """
    game_token = g.game_token
    reqs = scenario.win_reqs
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
            is_fulfilled = r.char.location_id == r.loc_id
            desc = f"👤 {r.char.name} must be at {maskable_name(r.loc)}"

        # --- CASE 3: ATTRIBUTE GOALS ---
        elif r.attrib_id:
            # Determine the subject (Character or Item)
            subject_id = r.char_id or r.item_id
            subject_name = r.char.name if r.char_id else (
                maskable_name(r.item) if r.item_id else None)
            icon = "👤" if r.char_id else ("📦" if r.item_id else "📊")
            subject_prefix = f"{icon} {subject_name}" if subject_name \
                else f"{icon} (Any Subject)"
            
            stmt = select(AttribVal).where(
                AttribVal.game_token == game_token,
                AttribVal.attrib_id == r.attrib_id
            )
            if subject_id:
                stmt = stmt.where(AttribVal.subject_id == subject_id)
            val_recs = db.session.scalars(stmt).all()
            current_vals = [rec.value for rec in val_recs] if val_recs else [0]

            if r.attrib.is_binary:
                is_fulfilled = any(
                    val == r.attrib_value for val in current_vals)
                have = "needs" if r.attrib_value > 0 else "cannot have"
                desc = f"{subject_prefix} {have} {r.attrib.name}"
            elif r.attrib.enum_entries:
                target_id = int(r.attrib_value)
                is_fulfilled = any(
                    int(val) == target_id for val in current_vals)
                state_name = r.attrib.format_value(r.attrib_value)
                desc = f"{subject_prefix} needs {r.attrib.name} " \
                       f"'{state_name}'"
            else:
                is_fulfilled = any(
                    val >= r.attrib_value for val in current_vals)
                desc = f"{subject_prefix} needs {r.attrib.name} ≥ " \
                       f"{format_num(r.attrib_value)}"

        if not is_fulfilled:
            all_met = False
        
        enriched_reqs.append({
            'description': desc,
            'fulfilled': is_fulfilled
        })

    return enriched_reqs, all_met
