import logging
from flask import g
from sqlalchemy.orm.attributes import flag_modified
from app.models import (
    db, GENERAL_ID, StorageType, Character, Location, Item, Pile, LocDest)
from app.utils import format_num, maskable_name
from app.src.logic_user_interaction import add_message

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# Coordinate & Grid Math
# ------------------------------------------------------------------------

def is_adjacent(pos, target_pos):
    """
    Chebyshev distance check. 
    Returns True if pos is within 1 square (including diagonals) of target_pos
    or if target_pos is None.
    """
    if not target_pos:
        return True
    if not pos:
        return False
    return abs(pos[0] - target_pos[0]) <= 1 and \
           abs(pos[1] - target_pos[1]) <= 1

def distance_between(pos, target_pos):
    """
    Returns None if one of the positions does not have a target.
    """
    if not pos or not target_pos:
        return None
    xdist = abs(pos[0] - target_pos[0])
    ydist = abs(pos[1] - target_pos[1])
    return Math.floor(sqrt(xdist**2 + ydist**2))

def is_in_grid(location, pos, check_zones=True):
    """
    Validates if a coordinate is within the location's physical boundaries.
    If check_zones is True, it also validates against zones that prevent travel.
    """
    if not location.dimensions or location.dimensions[0] == 0:
        return True
    width, height = location.dimensions
    if not pos:
        return False
    x, y = pos
    
    # 1. Check Outer Bounds (1-based indexing)
    if x < 1 or x > width or y < 1 or y > height:
        return False
        
    # 2. Check Zone Restrictions
    if check_zones:
        for zone in location.zones:
            if zone.prevents_travel:
                l, t, r, b = zone.coords
                if l <= x <= r and t <= y <= b:
                    return False 
            
    return True

def blocked_by_local_item(loc_id, pos):
    """Returns True if a StorageType.LOCAL item exists at the given coordinate."""
    game_token = g.game_token
    if not pos:
        return False
    x, y = pos

    blocking_pile = db.session.query(Pile).join(
        Item, (Pile.item_id == Item.id) & (Pile.game_token == Item.game_token)
    ).filter(
        Pile.game_token == game_token,
        Pile.owner_id == loc_id,
        Pile.position == [x, y],
        Item.storage_type == StorageType.LOCAL
    ).first()
    
    return blocking_pile is not None

def get_all_valid_coords(location):
    """Returns a list of all (x, y) tuples that are playable."""
    if not location.dimensions or location.dimensions[0] == 0:
        return []

    valid_coords = []
    width, height = location.dimensions
    
    # Iterate every square and check against exclusion logic
    for y in range(1, height + 1):
        for x in range(1, width + 1):
            pos = x, y
            if is_in_grid(location, pos):
                valid_coords.append(pos)
    return valid_coords

def get_default_position(location):
    """Finds the first available non-excluded square."""
    valid = get_all_valid_coords(location)
    return list(valid[0]) if valid else None

# ------------------------------------------------------------------------
# Party
# ------------------------------------------------------------------------

def get_moving_party(main_char, move_party=False):
    """
    Determines which characters are moving:
    - The main character.
    - Any characters at the same location with the same 'travel_party' name.
    """
    game_token = main_char.game_token
    party = {main_char}

    # If the user checked "Move with Party" and the character belongs to one
    if move_party and main_char.travel_party:
        group_members = Character.query.filter_by(
            game_token=game_token,
            travel_party=main_char.travel_party,
            location_id=main_char.location_id
        ).all()
        party.update(group_members)

    return list(party)

# ------------------------------------------------------------------------
# Character Movement At Location
# ------------------------------------------------------------------------

def move_group(main_char_id, dx, dy, move_party=False):
    """Moves an entire party relative to their current positions."""
    game_token = g.game_token
    main_char = Character.query.get((game_token, main_char_id))
    if not main_char or not main_char.location_id:
        return False, "Character not found."

    loc = Location.query.get((game_token, main_char.location_id))
    party = get_moving_party(main_char, move_party)
    
    results = {}
    for member in party:
        if not member.position: continue
        
        new_x = member.position[0] + dx
        new_y = member.position[1] + dy
        pos = [new_x, new_y]

        if is_in_grid(loc, pos) and not blocked_by_local_item(loc.id, pos):
            member.position = pos
            results[member.id] = member.position
    
    db.session.commit()
    return True, results

# ------------------------------------------------------------------------
# Inter-Location Travel
# ------------------------------------------------------------------------

def get_available_destinations(char):
    """
    Returns (reachable_destinations, has_nonadjacent_flag).
    """
    if not char.location:
        return [], False

    pos = char.position
    loc_id = char.location_id

    all_possible_exits = char.location.exits

    reachable = []
    has_nonadjacent = False

    for link in all_possible_exits:
        door_here = link.door_at(loc_id)
        if is_adjacent(pos, door_here):
            reachable.append(link)
        else:
            has_nonadjacent = True
            
    return reachable, has_nonadjacent

def check_location_access(char, loc):
    """Returns (True, "") or (False, "Reason") based on travel requirements."""
    game_token = char.game_token
    
    for req in loc.entrance_reqs:
        # 1. Check Universal Item (General Storage)
        if req.item_id:
            pile = Pile.query.filter_by(
                game_token=game_token, item_id=req.item_id, owner_id=GENERAL_ID
            ).first()
            current_qty = pile.quantity if pile else 0
            if current_qty < req.quantity:
                return False, f"Requires {format_num(req.quantity)}" \
                              f" {maskable_name(req.item)}"

        # 2. Check Character Attribute
        elif req.attrib_id:
            val_rec = AttribVal.query.filter_by(
                game_token=game_token, subject_id=char.id, attrib_id=req.attrib_id
            ).first()
            current_val = val_rec.value if val_rec else 0
            
            if req.attrib.is_binary or req.attrib.enum_list:
                if current_val != req.attrib_value:
                    return False, f"{char.name} must have {req.attrib.name}" \
                                  f" {req.attrib_value}"
            else:
                if current_val < req.attrib_value:
                    return False, f"{char.name} must have {req.attrib.name}" \
                                  f" {req.attrib_value}"
    return True, ""

def arrive_at_destination(main_char_id, dest_loc_id, move_party=False):
    """
    Teleports a party to a specific location if next to a door.
    Places them at the door coordinate if a link exists, otherwise default.
    """
    game_token = g.game_token
    main_char = Character.query.get((game_token, main_char_id))
    loc_here = main_char.location

    all_exits = loc_here.exits
    possible_links = [r for r in all_exits if r.other_loc(loc_here.id).id == dest_loc_id]
    if not possible_links:
        return False, "No path exists to that location."

    best_link = None
    for link in possible_links:
        door_here = link.door_at(loc_here.id)
        if not door_here or is_adjacent(main_char.position, door_here):
            best_link = link
            break

    if not best_link:
        target_name = possible_links[0].other_loc(loc_here.id).name
        return False, f"You are too far from the exit to {target_name}." \
                      " Move closer to the door icon."

    target_loc = best_link.other_loc(loc_here.id)
    can_enter, reason = check_location_access(main_char, target_loc)
    if not can_enter:
        return False, reason

    new_pos = best_link.door_at(dest_loc_id)
    if not new_pos:
        new_pos = get_default_position(target_loc)

    party = get_moving_party(main_char, move_party)
    for member in party:
        member.location_id = dest_loc_id
        member.position = new_pos
        member.dest_id = None # Clear "in-flight" destination
    
    if target_loc.masked:
        target_loc.masked = False

    db.session.commit()
    party = " and party" if main_char.travel_party and move_party else ''
    add_message(f"{main_char.name}{party} traveled to {target_loc.name}.")
    return True, "Arrived."
