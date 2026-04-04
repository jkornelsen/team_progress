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

def get_moving_party(main_char, move_with_ids=None):
    """
    Determines which characters are moving.
    Includes:
    1. The main character.
    2. Any character IDs explicitly passed in (from a 'Move With' dropdown).
    3. Any characters at the same location with the same 'travel_group' name.
    """
    game_token = main_char.game_token
    party = {main_char} # Use a set to avoid duplicates

    # 1. Add explicit IDs from the form
    if move_with_ids:
        for cid in move_with_ids:
            other = Character.query.get(game_token, (int(cid)))
            if other and other.location_id == main_char.location_id:
                party.add(other)

    # 2. Add via travel_group string
    if main_char.travel_group:
        group_members = Character.query.filter_by(
            game_token=game_token,
            travel_group=main_char.travel_group,
            location_id=main_char.location_id
        ).all()
        party.update(group_members)

    return list(party)

# ------------------------------------------------------------------------
# Character Movement At Location
# ------------------------------------------------------------------------

def move_group(main_char_id, dx, dy, move_with_ids=None):
    """Moves an entire party relative to their current positions."""
    game_token = g.game_token
    main_char = Character.query.get((game_token, main_char_id))
    if not main_char or not main_char.location_id:
        return False, "Main character not found or has no location."

    loc = Location.query.get((game_token, main_char.location_id))
    party = get_moving_party(main_char, move_with_ids)
    
    results = {}
    for member in party:
        new_x = member.position[0] + dx
        new_y = member.position[1] + dy

        if is_in_grid(loc, new_x, new_y):
            member.position = (new_x, new_y)
            results[member.id] = member.position
    
    db.session.commit()
    return True, results

# ------------------------------------------------------------------------
# Inter-Location Travel
# ------------------------------------------------------------------------

def get_available_destinations(char):
    """
    Returns (reachable_destinations, has_nonadjacent_flag).
    A destination is reachable only if the character is adjacent to its door.
    """
    game_token = g.game_token
    loc_id = char.location_id
    pos = char.position # [x, y]

    # Find all possible links for this location
    all_links = LocDest.query.filter(
        LocDest.game_token == game_token,
        ((LocDest.loc1_id == loc_id) | 
         ((LocDest.loc2_id == loc_id) & (LocDest.bidirectional == True)))
    ).all()

    reachable = []
    has_nonadjacent = False

    for link in all_links:
        # Determine which door coordinate belongs to the character's CURRENT location
        door_here = link.door1 if link.loc1_id == loc_id else link.door2
        
        # Adjacency check (Chebyshev distance <= 1)
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
    
    # 1. Find the specific link
    link = LocDest.query.filter(
        LocDest.game_token == game_token,
        ((LocDest.loc1_id == main_char.location_id) & (LocDest.loc2_id == dest_loc_id)) |
        ((LocDest.loc1_id == dest_loc_id) & (LocDest.loc2_id == main_char.location_id))
    ).first()
    if not link:
        return False, "No path exists to that location."

    # 2. ENFORCE ADJACENCY: Character must be near the door to use it
    door_here = link.door1 if link.loc1_id == main_char.location_id else link.door2
    if not is_adjacent(main_char.position, door_here):
        return False, "You are too far from the exit. Move closer to the door icon."

    # 3. Perform movement for the whole party
    party = get_moving_party(main_char, move_with_ids)
    target_loc = Location.query.get((game_token, dest_loc_id))
    
    for member in party:
        # Enter at the corresponding door in the new room
        new_pos = link.door2 if link.loc2_id == dest_loc_id else link.door1
        
        member.location_id = dest_loc_id
        member.position = new_pos
        member.dest_id = None # Clear "in-flight" destination
    
    if target_loc.masked:
        target_loc.masked = False

    db.session.commit()
    add_message(game_token, f"{main_char.name} traveled to {target_loc.name}.")
    return True, "Arrived."
