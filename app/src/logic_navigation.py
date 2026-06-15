from flask import g
import collections
import logging
import math
from sqlalchemy import and_, or_
from sqlalchemy.orm.attributes import flag_modified

from app.models import (
    db, GENERAL_ID, StorageType, Character, Location, Item, Pile,
    AttribVal, LocDest)
from app.utils import format_num, maskable_name
from app.src.logic_user_interaction import add_message

logger = logging.getLogger(__name__)

CENTER = (0, 0)
OFFSETS = [
    (1, 0),   # East
    (1, 1),   # SE
    (0, 1),   # South
    (-1, 1),  # SW
    (-1, 0),  # West
    (-1, -1), # NW
    (0, -1),  # North
    (1, -1)   # NE
]
NEIGHBORHOOD = [CENTER] + OFFSETS

# ------------------------------------------------------------------------
# Coordinate & Grid Math
# ------------------------------------------------------------------------

def grid_dist(pos1, pos2):
    """
    Grid Distance (Chebyshev). 
    Matches tactical movement: diagonals count as 1 step.
    """
    if not pos1 or not pos2:
        return 999 # Sentinel for "infinitely far"
    return max(
        abs(pos1[0] - pos2[0]),
        abs(pos1[1] - pos2[1]))

def is_adjacent(pos, target_pos):
    """
    Returns True if targets are within 1 step (including diagonals).
    """
    if not target_pos:
        return True # Non-positioned targets are always "adjacent"
    return grid_dist(pos, target_pos) <= 1

def straight_line_dist(pos, target_pos):
    """
    Geometric Distance (Euclidean). 
    Used for 'as the crow flies' calculations or range penalties.
    """
    if not pos or not target_pos:
        return None
    xdist = abs(pos[0] - target_pos[0])
    ydist = abs(pos[1] - target_pos[1])
    return math.floor(math.hypot(xdist, ydist))

def is_in_grid(loc, pos, check_zones=True):
    """
    Validates if a coordinate is within the location's physical boundaries.
    If check_zones is True, it also validates against zones that prevent travel.
    """
    if not loc.dimensions or loc.dimensions[0] == 0:
        return True
    width, height = loc.dimensions
    if not pos:
        return False
    x, y = pos
    
    # 1. Check Outer Bounds (1-based indexing)
    if x < 1 or x > width or y < 1 or y > height:
        return False
        
    # 2. Check Zone Restrictions
    if check_zones:
        for zone in loc.zones:
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

def is_cell_blocked(loc, pos, exclude_char_id=None):
    """
    Is this cell traversable?
    Checks boundaries, zones, local items, and other characters.
    """
    if not pos or not is_in_grid(loc, pos, check_zones=True):
        return True
    game_token = g.game_token
    
    # Check for LOCAL items (furniture/walls)
    if blocked_by_local_item(loc.id, pos):
        return True
        
    # Check for other characters
    other_char = Character.query.filter(
        Character.game_token == game_token,
        Character.location_id == loc.id,
        Character.position == list(pos)
    )
    if exclude_char_id:
        other_char = other_char.filter(Character.id != exclude_char_id)
    
    return other_char.first() is not None

def get_all_valid_coords(loc):
    """Returns a list of all (x, y) tuples that are playable."""
    if not loc.has_grid:
        return []

    valid_coords = []
    width, height = loc.dimensions
    
    # Iterate every square and check against exclusion logic
    for y in range(1, height + 1):
        for x in range(1, width + 1):
            pos = x, y
            if is_in_grid(loc, pos):
                valid_coords.append(pos)
    return valid_coords

def find_nearest_available_pos(
        loc, target_pos, exclude_char_id=None, max_radius=10):
    """
    Finds the closest non-blocked tile to target_pos using a spiral search.
    If target_pos itself is open, returns it.
    """
    if not loc.has_grid:
        return None
        
    if not target_pos:
        target_pos = [1, 1]

    # Spiral/Ring search
    for radius in range(max_radius + 1):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                # We only want to check the "crust" of the current square ring
                if abs(dx) != radius and abs(dy) != radius:
                    continue
                
                check_pos = [target_pos[0] + dx, target_pos[1] + dy]
                if not is_cell_blocked(loc, check_pos, exclude_char_id):
                    return check_pos
                    
    return None # Completely blocked area

def get_default_position(loc):
    """Finds the first available non-excluded square."""
    valid = get_all_valid_coords(loc)
    return list(valid[0]) if valid else None

def get_output_positions(loc, anchor_pos):
    """Returns an ordered list of [x, y] coordinates for placing an item."""
    if not anchor_pos or len(anchor_pos) != 2:
        return [None]
    x, y = anchor_pos

    candidates = []

    for dx, dy in NEIGHBORHOOD:
        cand = [x + dx, y + dy]
        if is_in_grid(loc, cand) and not blocked_by_local_item(loc.id, cand):
            candidates.append(cand)
    
    return candidates

def find_best_output_pos(item_id, loc_id, anchor_pos):
    """
    Finds the best coordinate adjacent to anchor_pos to place item_id.
    1. Checks for existing piles of item_id in adjacent squares.
    2. Finds first unblocked adjacent square.
    """
    if not anchor_pos or len(anchor_pos) != 2:
        return None

    game_token = g.game_token
    loc = db.session.get(Location, (game_token, loc_id))
    if not loc or not loc.dimensions:
        return None

    candidates = get_output_positions(loc, anchor_pos)

    # PHASE 1: Search for existing pile of the same item to merge into
    for candidate in candidates:
        existing = Pile.query.filter_by(
            game_token=game_token,
            owner_id=loc_id,
            item_id=item_id,
            position=candidate
        ).first()
        if existing:
            return candidate

    # PHASE 2: Find first unblocked square
    for candidate in candidates:
        if not blocked_by_local_item(loc_id, candidate):
            return candidate

    # Fallback: If everything is blocked, return the anchor position itself
    # so we don't end up at [1, 1]
    return anchor_pos

# ------------------------------------------------------------------------
# Party
# ------------------------------------------------------------------------

def get_neighbors(pos):
    """Returns all 8 adjacent coordinates."""
    x, y = pos
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            if dx == 0 and dy == 0: continue
            yield (x + dx, y + dy)

def get_cohesive_party(main_char, move_party_flag):
    """
    Finds matching party members that are physically connected 
    to the leader (max gap of 1 empty tile between members).
    """
    if not move_party_flag:
        return [main_char]

    game_token = g.game_token
    # 1. Get all potential candidates based on party settings
    party_name = main_char.travel_party
    my_name = main_char.name
    filters = []
    if party_name:
        filters.append(Character.travel_party == party_name)
        filters.append(Character.name == party_name)
    filters.append(Character.travel_party == my_name)

    candidates = Character.query.filter(
        Character.game_token == game_token,
        Character.location_id == main_char.location_id,
        Character.id != main_char.id,
        or_(*filters)
    ).all()

    # 2. Flood-fill to find connected components within distance 2
    # (Distance 1 = adjacent, Distance 2 = 1 empty tile gap)
    cohesive_group = {main_char}
    queue = collections.deque([main_char])
    
    while queue:
        current = queue.popleft()
        remaining_candidates = [c for c in candidates if c not in cohesive_group]
        for p in remaining_candidates:
            if grid_dist(current.position, p.position) <= 2:
                cohesive_group.add(p)
                queue.append(p)
                
    return list(cohesive_group)

def get_reachable_map(loc, start_pos, max_steps, current_obstacles):
    """
    BFS to find all reachable cells and the shortest distance to them.
    Respects walls, local items, and current character positions.
    """
    # Start pos is always reachable in 0 steps
    reachable = {tuple(start_pos): 0}
    queue = collections.deque([tuple(start_pos)])
    
    while queue:
        curr = queue.popleft()
        curr_dist = reachable[curr]
        
        if curr_dist < max_steps:
            for neighbor in get_neighbors(curr):
                if neighbor in reachable: continue
                
                # Pathfinding check: 
                # Is it in the grid? Not blocked by wall/item? Not occupied?
                if is_in_grid(loc, neighbor) and \
                   not blocked_by_local_item(loc.id, neighbor) and \
                   neighbor not in current_obstacles:
                    reachable[neighbor] = curr_dist + 1
                    queue.append(neighbor)
    return reachable

def get_moving_party(main_char, move_party=False):
    """
    Determines which characters are moving:
    - The main character.
    - Any characters sharing the same 'travel_party' string.
    - Any character whose 'name' is the 'travel_party' of the main character.
    - Any character whose 'travel_party' is the 'name' of the main character.
    """
    if not move_party:
        return [main_char]
    game_token = g.game_token

    party_name = main_char.travel_party
    my_name = main_char.name

    filters = []
    if party_name:
        filters.append(Character.travel_party == party_name) # Shared group name
        filters.append(Character.name == party_name)         # I am following them
    filters.append(Character.travel_party == my_name)        # They are following me

    group_members = Character.query.filter(
        Character.game_token == game_token,
        Character.location_id == main_char.location_id,
        or_(*filters)
    ).all()

    party = {main_char}
    party.update(group_members)
    return list(party)

# ------------------------------------------------------------------------
# Character Movement At Location
# ------------------------------------------------------------------------

def move_group(main_char_id, dx, dy, move_party=False):
    """
    Moves leader and party. If leader steps on a teammate, they swap 
    positions before the teammate calculates their own movement.
    """
    game_token = g.game_token
    main_char = db.session.get(Character, (game_token, main_char_id))
    if not main_char or not main_char.location_id:
        return False, "Character not found."

    loc = db.session.get(Location, (game_token, main_char.location_id))
    party = get_cohesive_party(main_char, move_party)
    followers = [c for c in party if c.id != main_char.id]
    
    # 1. Static obstacles: Walls and characters NOT in the moving group
    static_occupied = {
        tuple(c.position) for c in Character.query.filter(
            Character.game_token == game_token,
            Character.location_id == loc.id,
            Character.id.notin_([c.id for c in party])
        ).all() if c.position
    }
    
    results = {}
    leader_old_pos = tuple(main_char.position)
    leader_target = (leader_old_pos[0] + dx, leader_old_pos[1] + dy)

    # --- PHASE 1: MOVE THE LEADER & HANDLE SWAPS ---
    
    if not is_in_grid(loc, leader_target) or blocked_by_local_item(
            loc.id, leader_target) or leader_target in static_occupied:
        # Leader is physically blocked by the environment or an enemy
        # We still record their current pos to ensure followers flow toward them
        results[main_char.id] = main_char.position
    else:
        # Check if a group member is in the way
        occupant_to_bump = next(
            (f for f in followers
            if tuple(f.position) == leader_target), None)
        
        if occupant_to_bump:
            # DISPLACEMENT SWAP: Bump follower to leader's old spot
            occupant_to_bump.position = list(leader_old_pos)
            main_char.position = list(leader_target)
        else:
            # Standard move into empty space
            main_char.position = list(leader_target)
    
    results[main_char.id] = main_char.position
    
    # --- PHASE 2: MOVE FOLLOWERS ---
    
    # Track who has finished moving so they become obstacles for
    # the next person
    newly_settled = {tuple(main_char.position)}
    
    # Sort followers by distance to leader's NEW position so
    # the "front" of the pack moves first
    followers.sort(key=lambda c: grid_dist(c.position, main_char.position))

    for f in followers:
        # Budget: 2 steps if distant (>1), else 1 step.
        dist_to_leader = grid_dist(f.position, main_char.position)
        budget = 2 if dist_to_leader > 1 else 1
        
        # Obstacles = Environmental Static + teammates who already moved
        current_obstacles = static_occupied | newly_settled
        
        reachable = get_reachable_map(
            loc, f.position, budget, current_obstacles)
        
        if not reachable:
            # Can't move at all, stay at current (possibly bumped) position
            newly_settled.add(tuple(f.position))
            results[f.id] = f.position
            continue

        # Find reachable cell closest to leader
        best_pos = tuple(f.position)
        min_dist = grid_dist(best_pos, main_char.position)
        
        for r_pos, steps in reachable.items():
            d = grid_dist(r_pos, main_char.position)
            # Criteria:
            # 1. Closer to leader.
            # 2. If same distance, use fewer steps.
            if d < min_dist or (d == min_dist and steps < reachable[best_pos]):
                min_dist = d
                best_pos = r_pos

        f.position = list(best_pos)
        newly_settled.add(best_pos)
        results[f.id] = f.position

    db.session.flush()
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

def check_location_access(party, loc):
    """Returns (True, "") or (False, "Reason") based on travel requirements."""
    game_token = g.game_token
    
    for req in loc.entrance_reqs:
        # Universal Items
        if req.item_id and req.item.storage_type == StorageType.UNIVERSAL:
            pile = Pile.query.filter_by(
                game_token=game_token,
                item_id=req.item_id,
                owner_id=GENERAL_ID
            ).first()
            current_qty = pile.quantity if pile else 0
            if current_qty < req.val_required:
                return False, f"Requires {format_num(req.val_required)}" \
                              f" {maskable_name(req.item)}"
            continue

        satisfied = False
        error_msg = ""

        for char in party:
            # Carried Item Check: One member must have a pile >= requirement
            if req.item_id and req.item.storage_type == StorageType.CARRIED:
                pile = Pile.query.filter_by(
                    game_token=game_token,
                    item_id=req.item_id,
                    owner_id=char.id
                ).first()
                if pile and pile.quantity >= req.val_required:
                    satisfied = True
                    break
                error_msg = f"Must carry {format_num(req.val_required)}" \
                            f" {maskable_name(req.item)}."

            # Character Attributes
            elif req.attrib_id:
                av = AttribVal.query.filter_by(
                    game_token=game_token,
                    subject_id=char.id,
                    attrib_id=req.attrib_id
                ).first()
                current_val = av.value if av else 0
                
                if req.attrib.is_binary or req.attrib.enum_list:
                    if current_val == req.val_required:
                        satisfied = True
                        break
                else:
                    if current_val >= req.val_required:
                        satisfied = True
                        break
                req_val_display = req.attrib.enum_list[int(req.val_required)] \
                    if req.attrib.enum_list else req.val_required
                error_msg = f"Must have {req.attrib.name} {req_val_display}"

        if not satisfied:
            return False, error_msg

    return True, ""

def arrive_at_destination(main_char_id, dest_loc_id, move_party=False):
    """
    Teleports a party to a specific location if next to a door.
    Places them at the door coordinate if a link exists, otherwise default.
    """
    game_token = g.game_token
    main_char = db.session.get(Character, (game_token, main_char_id))
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

    party = get_moving_party(main_char, move_party)
    target_loc = best_link.other_loc(loc_here.id)
    can_enter, reason = check_location_access(party, target_loc)
    if not can_enter:
        return False, reason

    new_pos = best_link.door_at(dest_loc_id)
    if not new_pos:
        new_pos = get_default_position(target_loc)

    for member in party:
        member.location_id = dest_loc_id
        member.position = new_pos
        member.dest_id = None # Clear "in-flight" destination
    
    if target_loc.masked:
        target_loc.masked = False

    db.session.flush()
    party = " and party" if main_char.travel_party and move_party else ''
    add_message(f"{main_char.name}{party} traveled to {target_loc.name}.")
    return True, "Arrived."

