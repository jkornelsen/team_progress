import logging
from flask import g
from sqlalchemy.orm.attributes import flag_modified
from app.models import db, Character, Location, LocDest
from app.src.logic_user_interaction import add_message

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# Coordinate & Grid Math
# ------------------------------------------------------------------------

def is_adjacent(pos1, pos2):
    """
    Standard Chebyshev distance check. 
    Returns True if pos1 is within 1 square (including diagonals) of pos2.
    """
    if not pos1 or not pos2:
        return False
    return abs(pos1[0] - pos2[0]) <= 1 and abs(pos1[1] - pos2[1]) <= 1

def is_in_grid(location, x, y):
    """
    Validates if a coordinate is within bounds and NOT in an excluded zone.
    Exclusions are defined as [left, top, right, bottom].
    """
    if not location.dimensions or location.dimensions[0] == 0:
        return True # Non-grid location

    width, height = location.dimensions
    
    # 1. Check Outer Bounds (1-based indexing)
    if x < 1 or x > width or y < 1 or y > height:
        return False
        
    # 2. Check Exclusion Rectangle (L, T, R, B)
    if location.excluded and len(location.excluded) == 4:
        l, t, r, b = location.excluded
        # If the point falls inside the exclusion box, it is NOT in the grid
        if l <= x <= r and t <= y <= b:
            return False 
            
    return True

def get_all_valid_coords(location):
    """Returns a list of all (x, y) tuples that are playable."""
    if not location.dimensions or location.dimensions[0] == 0:
        return []

    valid_coords = []
    width, height = location.dimensions
    
    # Iterate every square and check against exclusion logic
    for y in range(1, height + 1):
        for x in range(1, width + 1):
            if is_in_grid(location, x, y):
                valid_coords.append((x, y))
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

        if is_in_grid(loc, new_x, new_y):
            member.position = [new_x, new_y]
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

def arrive_at_destination(main_char_id, dest_loc_id, move_with_ids=None):
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

    new_pos = best_link.door_at(dest_loc_id)
    target_loc = best_link.other_loc(loc_here.id)
    if not new_pos:
        new_pos = get_default_position(target_loc)

    party = get_moving_party(main_char, move_with_ids)
    for member in party:
        member.location_id = dest_loc_id
        member.position = new_pos
        member.dest_id = None # Clear "in-flight" destination
    
    if target_loc.masked:
        target_loc.masked = False

    db.session.commit()
    party = " and party" if main_char.travel_party else ''
    add_message(
        game_token, f"{main_char.name}{party} traveled to {target_loc.name}.")
    return True, "Arrived."
