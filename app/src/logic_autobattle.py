import random
import logging
from flask import g
from app.models import (
    db, Character, Location, Attrib, AttribVal, Event, 
    AutobattleField, AutobattleStage, Participant,
    OutcomeType)
from .logic_event import (
    roll_for_outcome, resolve_effects, process_all_auto_effects,
    get_chain_results)
from .logic_user_interaction import add_message

logger = logging.getLogger(__name__)

def is_autobattle_enabled():
    """Returns True if any location has autobattle enabled."""
    return Location.query.filter_by(
        game_token=g.game_token, 
        autobattle=True
    ).first() is not None

def get_battle_participants(loc_id):
    """Groups characters at a location by party."""
    chars = Character.query.filter_by(
        game_token=g.game_token, location_id=loc_id).all()
    parties = {}
    for c in chars:
        p_name = c.party or "Unformatted"
        parties.setdefault(p_name, []).append(c)
    return parties

def get_char_stat(char, field_type):
    """Helper to find HP, Max HP, etc. based on Attrib configuration."""
    attr = Attrib.query.filter_by(
        game_token=char.game_token, 
        ab_field=field_type
    ).first()
    if not attr: return 0
    
    val = AttribVal.query.filter_by(
        game_token=char.game_token, 
        subject_id=char.id, 
        attrib_id=attr.id
    ).first()
    return val.value if val else 0

def get_missing_hp(char):
    max_hp = get_char_stat(char, AutobattleField.MAX_HP)
    current_hp = get_char_stat(char, AutobattleField.HP)
    return max_hp - current_hp

def execute_event_chain(event_id, role_entities, depth=0):
    """
    Executes an event and recursively follows any eligible chains.
    'depth' prevents accidental infinite loops in configuration.
    Returns False if no event could be executed.
    """
    if depth > 5:
        logger.warning(f"Event chain reached max depth at event {event_id}")
        return True

    game_token = g.game_token
    event = db.session.get(Event, (game_token, event_id))
    if not event:
        return False

    # 1. Roll the outcome
    # For now, autobattle uses 'Normal' difficulty (0.5) for Four-Way rolls
    if event.outcome_type == OutcomeType.ROLLER:
        # Defaulting to 1d20 for system rolls if unspecified
        result_val, result_str, tier = roll_for_system_outcome(event_id)
    else:
        result_val, result_str, tier = roll_for_outcome(
            event_id, role_entities, difficulty=0.5, group_messages=False)
    if result_val is None:
        #add_message(result_str)
        return False

    # 2. Apply the effects (HP changes, status updates, etc.)
    # resolve_effects gives us the ledger (virtual state change)
    # needed to evaluate the next link
    resolved_effects, ledger = resolve_effects(
            event, role_entities, result_val, tier)
    process_all_auto_effects(event, role_entities, result_val, tier)
    
    # 3. Check for Chained Events
    # get_chain_results checks link requirements (e.g., 'If Success') 
    # against the current roll and the ledger
    chains = get_chain_results(event, role_entities, result_val, tier, ledger)
    
    if chains:
        # If multiple branches are eligible, pick one randomly
        next_event = random.choice(chains)
        execute_event_chain(next_event['child_id'], role_entities, depth + 1)
    return True

def run_battle_round(loc_id):
    """
    Executes one round of combat.
    1. Before Turn (DoTs)
    2. Turn Actions (Attacks)
    3. After Turn (Death Checks)
    """
    parties = get_battle_participants(loc_id)
    if len(parties) < 2:
        return False, "Need at least two opposing parties."

    all_chars = [c for p in parties.values() for c in p]
    # Sort by a generic initiative or just ID for now
    all_chars.sort(key=lambda x: x.id)

    for actor in all_chars:
        if get_char_stat(actor, AutobattleField.HP) < 1:
            continue

        # Before Turn (DoTs)
        before_actions = [
            e for e in actor.abilities 
            if e.ab_stage == AutobattleStage.BEFORE
        ]
        for act in before_actions:
            execute_event_chain(act.id, {
                Participant.SUBJECT: actor.id,
                Participant.AT: loc_id
            })

        if get_char_stat(actor, AutobattleField.HP) < 1:
            continue

        # Action Selection
        # Find abilities marked for 'turn' stage
        available_actions = [
            e for e in actor.abilities 
            if e.ab_stage == AutobattleStage.TURN and e.ab_priority > 0
        ]
        if not available_actions:
            continue

        # Target Selection
        # Find someone NOT in the actor's party with HP
        enemies = []
        for p_name, members in parties.items():
            if p_name != actor.party:
                enemies.extend([
                    m for m in members
                    if get_char_stat(m, AutobattleField.HP) >= 1])
        
        if enemies:
            # Sort enemies by missing HP descending
            # (most damaged first)
            enemies.sort(key=get_missing_hp, reverse=True)
            target = enemies[0]

            # Priority Sorting & Tie-Breaking
            # Shuffle first to randomize tie-breakers, then stable sort
            # by priority descending
            ordered_actions = list(available_actions)
            random.shuffle(ordered_actions)
            ordered_actions.sort(key=lambda e: e.ab_priority, reverse=True)

            # Execution Loop
            # Try higher priority values first;
            # move to the next if execution fails
            for action in ordered_actions:
                role_entities = {
                    Participant.SUBJECT: actor.id,
                    Participant.TARGET: target.id,
                    Participant.AT: loc_id
                }
                executed = execute_event_chain(action.id, role_entities)
                if executed:
                    # Successfully executed an action,
                    # end the turn attempts
                    break

    for actor in all_chars:
        # After Turn (Death Checks/Cleanup)
        after_actions = [
            e for e in actor.abilities
            if e.ab_stage == AutobattleStage.AFTER]
        for act in after_actions:
            execute_event_chain(
                act.id, {
                    Participant.SUBJECT: actor.id,
                    Participant.AT: loc_id})

    db.session.commit()
    return True, "Round completed."

def run_battle_reset(loc_id):
    """Executes 'reset' stage events for all characters at the location."""
    parties = get_battle_participants(loc_id)
    all_chars = [c for p in parties.values() for c in p]

    for actor in all_chars:
        reset_actions = [
            e for e in actor.abilities
            if e.ab_stage == AutobattleStage.RESET
        ]
        for act in reset_actions:
            execute_event_chain(
                act.id, {
                    Participant.SUBJECT: actor.id,
                    Participant.AT: loc_id
                }
            )
    
    db.session.commit()
    return True
