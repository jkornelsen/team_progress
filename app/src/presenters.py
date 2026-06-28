from flask import g, session
from app.models import (
    db, GENERAL_ID, EQUIPMENT_SLOTS_ID, StorageType,
    Entity, Item, Character, Location, Attrib,
    Pile, AttribVal, Progress, Overall, Recipe)
from app.utils import (
    ContextIds, LinkLetters, capture_origin, 
    sort_by_name_stripped, name_stripped)
from app.src.logic_production import (
    find_best_host, resolve_recipe_sources, can_perform_recipe)
from app.src.logic_navigation import is_adjacent
import logging

logger = logging.getLogger(__name__)

class ItemPlayPresenter:
    def __init__(self, item_id, req):
        self.req = req
        self.game_token = g.game_token
        self.item_id = item_id
        self.item = db.get_or_404(Item, (self.game_token, item_id))
        
        self.owner_id = self._resolve_owner_id(req)
        self.owner = db.session.get(Entity, (self.game_token, self.owner_id))

        self.ctx = self._reconcile_context(req)
        logger.debug(f"ctx.loc_id={self.ctx.loc_id}")
        self.pile = self._get_pile()

        capture_origin(name=self.item.name)

        logger.debug(
            f"---- play_item() ----\n"
            f"Item:{self.item.id} | Owner:{self.owner_id}"
            f" | Char:{self.ctx.char_id} | Loc:{self.ctx.loc_id}")

    def _resolve_owner_id(self, req):
        """
        Determine Viewed Pile Owner.
        This is the pile displayed at the top of the page.
        """
        owner_id = req.get_int('owner_id')
        if owner_id:
            return owner_id
            
        if self.item.storage_type == StorageType.UNIVERSAL:
            return GENERAL_ID
        if session.get('old_char_id'):
            return session.get('old_char_id')
        return session.get('old_loc_id') or GENERAL_ID

    def _reconcile_context(self, req):
        """Capture and clean session IDs."""
        char_id = req.get_int('char_id') or session.get('old_char_id')
        loc_id = req.get_int('loc_id') or session.get('old_loc_id')
        if self.owner.entity_type == Character.TYPENAME:
            char_id = self.owner.id
        elif self.owner.entity_type == Location.TYPENAME:
            loc_id = self.owner.id

        self.ctx_char = None
        char = db.session.get(
            Character, (self.game_token, char_id)) if char_id else None
        if char and self.owner.entity_type == Location.TYPENAME \
                and char.location_id != self.owner.id:
            # Clear character if they aren't here
            char = None
            char_id = None
            session.pop('old_char_id', None)
        elif char:
            # Override current location
            loc_id = char.location_id
            session['old_char_id'] = char_id
            self.ctx_char = char

        if self.owner.entity_type == Location.TYPENAME or self.ctx_char:
            logger.debug(f"old_loc_id {session.get('old_loc_id')} -> {loc_id}")
            session['old_loc_id'] = loc_id
        self.ctx_loc = db.session.get(
            Location, (self.game_token, loc_id)) if loc_id else None

        return ContextIds(
            owner_id=self.owner_id,
            char_id=char_id,
            loc_id=loc_id)

    def _get_pile(self):
        """Finds the specific stack on the grid or in inventory."""
        query = Pile.query.filter_by(
            game_token=self.game_token,
            item_id=self.item.id,
            owner_id=self.owner_id)
        pos = self.req.get_coords('pos')
        if pos:
            query = query.filter_by(position=list(pos))
        
        pile = query.first()
        return pile or Pile(
            item_id=self.item.id,
            owner_id=self.owner_id,
            quantity=0.0,
            position=pos)

    def _check_attrib_req(self, req, scope_ids, lookup):
        """Find the best candidate value for an attribute req."""
        req_met = False
        current_val = None
        entity_with_value = None
        satisfying_entity = None

        for eid in scope_ids:
            av = lookup.get((eid, req.attrib_id))
            if av is not None:
                if req.in_range(av.value):
                    req_met = True
                    current_val = av.value
                    satisfying_entity = eid
                    break
                
                # If failing, track the most relevant fail value
                if current_val is None:
                    current_val = av.value
                    entity_with_value = eid
                elif req.max_val != float('inf') and req.max_val < 0:
                    if av.value < current_val:
                        current_val = av.value
                        entity_with_value = eid
                elif av.value > current_val:
                    current_val = av.value
                    entity_with_value = eid
        
        return {
            'attrib': req.attrib,
            'range_display': req.range_display,
            'current_val': current_val,
            'is_satisfied': req_met,
            'link_entity_id': satisfying_entity or entity_with_value
        }

    def _enrich_recipe(self, r):
        """Resolves one recipe. Returns (enriched_dict, discovered_ids)."""
        host_id = find_best_host(r, self.owner_id, self.ctx)
        can_do, reason = can_perform_recipe(host_id, r, self.owner_id, self.ctx)
        resolved = resolve_recipe_sources(host_id, r, self.ctx)

        discovered_ids = set()
        sources_ui = []
        for res in resolved:
            if res['anticipated_owner_id'] != GENERAL_ID:
                discovered_ids.add(res['anticipated_owner_id'])
            discovered_ids.add(res['item'].id)

            url_params = {'owner_id': res['anticipated_owner_id']}
            if self.ctx.addl_char_id and \
                    res['anticipated_owner_type'] != Character.TYPENAME:
                url_params['char_id'] = self.ctx.char_id
            elif self.ctx.addl_loc_id and \
                    res['anticipated_owner_id'] == GENERAL_ID:
                url_params['loc_id'] = self.ctx.loc_id
            if res['best_pile'] and res['best_pile'].position:
                url_params['pos[]'] = res['best_pile'].position

            sources_ui.append({
                'ingredient': res['item'],
                'q_required': res['source_def'].q_required,
                'preserve': res['source_def'].preserve,
                'current_stock': res['total_available'],
                'pile_owner_id': res['anticipated_owner_id'],
                'pile_owner_type': res['anticipated_owner_type'],
                'url_params': url_params
            })

        possible = [
            int(s['current_stock'] // s['q_required'])
            for s in sources_ui if not s['preserve'] and s['q_required'] > 0]
        max_batches = max(1, min(possible)) if (
            can_do and possible) else (1 if can_do else 0)

        host_ent = db.session.get(
            Entity, (self.game_token, host_id)) if host_id else None

        enriched = {
            'recipe': r,
            'host_id': host_id,
            'host_name': host_ent.name if host_ent else "No Host",
            'can_produce': can_do,
            'reason': reason,
            'sources': sources_ui,
            'max_batches': max_batches,
            'attrib_reqs': [],  # filled by caller
            'byproducts': [{
                'item': bp.item,
                'rate_amount': bp.rate_amount,
                'url_params': self.ctx.get_params()} for bp in r.byproducts]
        }
        return enriched, discovered_ids

    def _base_attrib_scope(self):
        """Entity IDs always in scope for attrib checking, regardless of recipe."""
        scope = set()
        if self.owner_id != GENERAL_ID:
            scope.add(self.owner_id)
        if self.ctx.addl_char_id:
            scope.add(self.ctx.char_id)
        if self.ctx.addl_loc_id:
            scope.add(self.ctx.loc_id)
        # Blueprint ingredient IDs from recipe definitions
        for r in self.item.recipes:
            for source in r.sources:
                scope.add(source.item_id)
        return scope

    def _build_attrib_lookup(self, all_ids):
        """Single DB query for all AttribVals across the given ID set."""
        vals = AttribVal.query.filter(
            AttribVal.game_token == self.game_token,
            AttribVal.subject_id.in_([i for i in all_ids if i])
        ).all()
        return {(av.subject_id, av.attrib_id): av for av in vals}

    def _recipe_attrib_scope(self, r_data, base_scope):
        """Per-recipe scope: base + the specific owners/items resolved for this recipe."""
        scope = base_scope.copy()
        for src in r_data['sources']:
            if src['pile_owner_id'] != GENERAL_ID:
                scope.add(src['pile_owner_id'])
            scope.add(src['ingredient'].id)
        return scope

    def get_template_context(self):
        overall = db.session.get(Overall, self.game_token)

        # Enrich recipes, collecting all entity IDs seen across all recipes
        base_scope = self._base_attrib_scope()
        enriched_recipes = []
        all_discovered_ids = set()
        for r in self.item.recipes:
            enriched, discovered = self._enrich_recipe(r)
            enriched_recipes.append(enriched)
            all_discovered_ids.update(discovered)

        # One attrib lookup query now that we know all relevant IDs
        lookup = self._build_attrib_lookup(base_scope | all_discovered_ids)

        # Evaluate attrib reqs per recipe against their specific scope
        for r, r_data in zip(self.item.recipes, enriched_recipes):
            recipe_scope = self._recipe_attrib_scope(r_data, base_scope)
            r_data['attrib_reqs'] = [
                self._check_attrib_req(ar, recipe_scope, lookup)
                for ar in r.attrib_reqs]

        # Check if any masked item might be unmasked by this item
        has_masked_dependents = False
        if self.pile.quantity <= 0:
            has_masked_dependents = any(
                item.masked for item in (
                    db.session.get(
                        Item, (self.game_token, link.recipe.product_id))
                    for link in self.item.as_ingredient
                ) if item is not None
            )

        # Reverse dependencies
        used_for_production, seen_ids = [], {self.item_id}
        for source_link in self.item.as_ingredient:
            prod = source_link.recipe.product
            if prod.id not in seen_ids:
                used_for_production.append({
                    'item': prod,
                    'q_required': source_link.q_required,
                    'preserve': source_link.preserve,
                    'url_params': self.ctx.get_params()})
                seen_ids.add(prod.id)

        # Other characters here (Give button)
        other_chars_here = []
        if isinstance(self.owner, Character) and self.owner.location_id:
            raw_chars = Character.query.filter_by(
                game_token=self.game_token,
                location_id=self.owner.location_id).filter(
                Character.id != self.owner.id).all()
            for c in raw_chars:
                c.is_reachable = is_adjacent(
                    self.owner.position, c.position) \
                    if self.owner.position else True
                other_chars_here.append(c)

        # Reachability
        is_reachable, reach_error = True, None
        if self.ctx_char:
            if self.owner.entity_type == 'location' \
                    and self.owner.dimensions \
                    and self.owner.dimensions[0] > 0:
                if self.pile.position:
                    if not is_adjacent(
                            self.ctx_char.position, self.pile.position):
                        is_reachable = False
                        reach_error = "Must be next to item to pick up."
                else:
                    is_reachable = False
                    reach_error = "This item is not currently placed on the grid."
            elif self.owner.entity_type == Character.TYPENAME \
                    and self.owner.id != self.ctx_char.id:
                if self.owner.location_id == self.ctx_char.location_id:
                    if not is_adjacent(
                            self.ctx_char.position, self.owner.position):
                        is_reachable = False
                        reach_error = f"Must be next to {self.owner.name} to trade."
                else:
                    is_reachable = False
                    reach_error = f"{self.owner.name} is in a different location."

        # attribreq_entities lookup for tooltip links
        attribreq_entities = {self.owner.id: self.owner}
        if self.ctx_char:
            attribreq_entities[self.ctx_char.id] = self.ctx_char
        if self.ctx_loc:
            attribreq_entities[self.ctx_loc.id] = self.ctx_loc  # already correct
        for eid in base_scope | all_discovered_ids:
            if eid not in attribreq_entities and eid != GENERAL_ID:
                ent = db.session.get(Entity, (self.game_token, eid))
                if ent:
                    attribreq_entities[eid] = ent

        slots_attr = db.session.get(
            Attrib, (self.game_token, EQUIPMENT_SLOTS_ID))
        available_slots = slots_attr.enum_entries if slots_attr else []

        return {
            "item": self.item,
            "owner": self.owner,
            "pile": self.pile,
            "ctx_char": self.ctx_char,
            "ctx_loc": self.ctx_loc,
            "enriched_recipes": enriched_recipes,
            "used_for_production": sort_by_name_stripped(
                used_for_production, lambda d: d['item']),
            "byproduct_of": [{
                'item': bl.recipe.product,
                'rate_amount': bl.rate_amount,
                'url_params': self.ctx.get_params()}
                for bl in self.item.as_byproducts],
            "progress": Progress.query.filter_by(
                game_token=self.game_token, product_id=self.item_id).first(),
            "available_slots": available_slots,
            "other_chars_here": other_chars_here,
            "is_reachable": is_reachable,
            "reach_error": reach_error,
            "attribreq_entities": attribreq_entities,
            "attrib_values": sort_by_name_stripped(
                AttribVal.query.filter_by(
                    game_token=self.game_token,
                    subject_id=self.item_id).all(),
                lambda p: p.attrib),
            "has_masked_dependents": has_masked_dependents,
            "link_letters": LinkLetters(excluded='moedpqrg')
        }
