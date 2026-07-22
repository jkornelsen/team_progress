import logging
from datetime import datetime
from sqlalchemy import select, event as sa_event, inspect as sa_inspect
from sqlalchemy.dialects.postgresql import ARRAY, NUMRANGE
from .database import db

logger = logging.getLogger(__name__)

# Reserved ID in Entity table for universal storage.
# Can have up to one general pile for each Item.
# Progress can also be hosted generally rather than by char or at loc.
GENERAL_ID = 1

# Reserved ID in the Attrib table for equipment slots.
# Unlike the General ID, this record doesn't get created unless needed.
EQUIPMENT_SLOTS_ID = 2
HIGHEST_RESERVED_ID = EQUIPMENT_SLOTS_ID

class StorageType:
    """How Item objects are placed."""
    UNIVERSAL = 'u' # not anchored anywhere
    LOCAL     = 'l' # stationary at a Location
    CARRIED   = 'c' # can be held by Character or on floor at Location

    ALL_CODES = (UNIVERSAL, LOCAL, CARRIED)

class JsonKeys:
    ENTITIES = 'entities'
    GENERAL = 'general data'
    OVERALL = 'overall settings'

# Key: (game_token, attrib_id) → Value: {label: entry_id}
_enum_cache: dict[tuple, dict[str, int]] = {}

# ------------------------------------------------------------------------
# Model Utilities
# ------------------------------------------------------------------------

@sa_event.listens_for(db.Model, 'init', propagate=True)
def auto_init_defaults(target, args, kwargs):
    """
    Whenever a new Model instance is created, look for columns 
    with defaults and apply them to the Python object immediately.
    """
    for column in target.__table__.columns:
        if column.default is not None and column.name not in kwargs:
            if callable(column.default.arg):
                setattr(target, column.name, column.default.arg(None))
            else:
                setattr(target, column.name, column.default.arg)

def deep_rel(attr, child_cls, fk_field):
    """Helper to format deep relationship dictionary entries."""
    return {attr: (child_cls, fk_field)}

def scrub_array(data, field_name, min_length=2):
    val = data.get(field_name)
    if (isinstance(val, (list, tuple)) and 
        len(val) >= min_length and 
        any(v > 0 for v in val)):
        data[field_name] = tuple(val)
    else:
        data[field_name] = None

def resolve_enum_id(game_token, attrib_id, label):
    cache_key = (game_token, attrib_id)
    if cache_key in _enum_cache:
        return _enum_cache[cache_key].get(label)
    with db.session.no_autoflush:
        entry = EnumEntry.query.filter_by(
            game_token=game_token, attrib_id=attrib_id, label=label).first()
    return entry.id if entry else None

def prime_enum_cache(game_token):
    """Call after flushing attribs but before hydrating items/piles."""
    clear_enum_cache(game_token)
    entries = EnumEntry.query.filter_by(game_token=game_token).all()
    for e in entries:
        key = (game_token, e.attrib_id)
        _enum_cache.setdefault(key, {})[e.label] = e.id

def clear_enum_cache(game_token=None):
    if game_token:
        keys = [k for k in _enum_cache if k[0] == game_token]
        for k in keys:
            del _enum_cache[k]
    else:
        _enum_cache.clear()

def attrib_val_to_json(game_token, attrib_id, raw_val):
    """Converts a DB float to a JSON-friendly string, bool, or float."""
    if attrib_id is None or raw_val is None:
        return raw_val
        
    attrib = db.session.get(Attrib, (game_token, attrib_id))
    if not attrib:
        return raw_val

    if attrib.is_binary:
        return bool(raw_val)
    
    if attrib.enum_entries:
        return attrib.format_value(raw_val)
        
    return raw_val

def attrib_val_from_json(game_token, attrib_id, json_val):
    """Converts a JSON value (str/bool/num) back to a DB float."""
    if attrib_id is None or json_val is None or json_val == "":
        return json_val

    if isinstance(json_val, str):
        # It's an enum label, resolve it to an entry ID
        val = resolve_enum_id(game_token, attrib_id, json_val)
        return float(val) if val is not None else 0.0
    
    if isinstance(json_val, bool):
        return 1.0 if json_val else 0.0
        
    try:
        return float(json_val)
    except (ValueError, TypeError):
        return 0.0

class DictHydrator:
    """Map JSON keys to DB columns."""
    LEGACY_KEYS = {
        # e.g. 'old_key': 'new_key',
    }
    LEGACY_VALUES = {
        # e.g. 'key': {'old_value': 'new_value'},
    }

    @classmethod
    def from_dict(cls, data, game_token, **overrides):
        # Skip lists and dicts because they may be nested relationships,
        # except for ARRAY DB column type.
        # Subclass from_dict() methods will need to handle skipped data.
        fields = {}
        if not isinstance(data, dict):
            raise ValueError(
                f"Expected a dict but got {type(data).__name__}: {repr(data)}")

        # Log any unrecognized scalar fields
        insp = sa_inspect(cls).mapper
        column_names = {col.key for col in insp.column_attrs}
        relationship_names = {rel.key for rel in insp.relationships}
        known_names = column_names | relationship_names
        for k in data:
            if k not in known_names and not isinstance(data[k], (list, dict)):
                logger.warning(
                    f"[{cls.__name__}.from_dict] Unrecognized field '{k}' "
                    f"({data[k]!r})."
                )

        # Legacy self-healing
        for old_key, new_key in cls.LEGACY_KEYS.items():
            if old_key in data:
                data[new_key] = data.pop(old_key)
        for key, values_map in cls.LEGACY_VALUES.items():
            if key in data:
                current_val = data[key]
                if current_val in values_map:
                    data[key] = values_map[current_val]

        # Set simple values
        for k, v in data.items():
            if hasattr(cls, k):
                column = cls.__table__.columns.get(k)
                is_array_col = column is not None and isinstance(column.type, ARRAY)
                if not isinstance(v, (list, dict)) or is_array_col:
                    fields[k] = v
        
        # Apply mandatory IDs or overrides
        fields.update(overrides)

        # If this model needs a manual ID and doesn't have one, generate it.
        # This applies to entities and Recipe.
        if hasattr(cls, 'id') and fields.get('id') is None:
            if cls in list(ENTITIES.values()) + [Recipe]:
                fields['id'] = IdSequence.generate_next_id(game_token)

        return cls(game_token=game_token, **fields)

    def _get_column_default(self, col_name):
        """Return the default value for a column, or a sentinel if none."""
        _MISSING = object()  # defined at module level, not inline
        column = self.__table__.columns.get(col_name)
        if column is None or column.default is None:
            return _MISSING
        arg = column.default.arg
        return arg(None) if callable(arg) else arg

    def to_dict_sparse(self, data: dict) -> dict:
        """Strip keys whose values are empty or match the column default."""
        return {
            k: v for k, v in data.items()
            if v is not None
            and v != ""
            and v != []
            and v != {}
            and v != self._get_column_default(k)
        }

def timeToStr(dt_obj):
    return dt_obj.isoformat() if dt_obj else None

def timeFromStr(date_str):
    if not date_str:
        return None
    return datetime.fromisoformat(date_str)

# ------------------------------------------------------------------------
# Entity Parent Class
# ------------------------------------------------------------------------

class Entity(db.Model, DictHydrator):
    """Base table for all primary game objects."""
    TYPENAME = 'entity'
    PLURAL = f'entities'
    SHORT = 'ent'
    __tablename__ = PLURAL

    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    def to_dict(self):
        """Base export for shared entity fields."""
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "attribs": sorted([
                [av.attrib_id, av.serialized_value] 
                for av in self.attrib_values
            ], key=lambda x: x[0]),
            "abilities": sorted(
                [link.event_id for link in self._ability_links])
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, **overrides):
        """Standard base hydration for all entities."""
        entity = super().from_dict(data, game_token, **overrides)
        for a_data in data.get('attribs', []):
            if isinstance(a_data, list) and len(a_data) == 2:
                attrib_id, raw_val = a_data[0], a_data[1]
                final_val = attrib_val_from_json(
                    game_token, attrib_id, raw_val)
                if final_val is not None:
                    entity.attrib_values.append(AttribVal(
                        game_token=game_token,
                        attrib_id=attrib_id,
                        value=final_val
                    ))
        for event_id in data.get('abilities', []):
            entity._ability_links.append(EntityAbility(
                game_token=game_token,
                event_id=event_id
            ))
        return entity

    @classmethod
    def get_or_new(cls, game_token, id):
        if id is None:
            # Create a fresh instance.
            # Does not generate id or write to db.
            return cls(game_token=game_token)
        return cls.query.filter_by(game_token=game_token, id=id).first_or_404()

    @property
    def abilities(self):
        return [link.event for link in self._ability_links]

    piles = db.relationship(
        'Pile', 
        back_populates='owner', 
        foreign_keys="[Pile.game_token, Pile.owner_id]",
        cascade="all, delete-orphan") # Deletes piles if Entity is deleted
    attrib_values = db.relationship(
        'AttribVal', 
        back_populates='subject',
        foreign_keys="[AttribVal.game_token, AttribVal.subject_id]",
        cascade="all, delete-orphan")
    _ability_links = db.relationship(
        'EntityAbility',
        back_populates='entity',
        foreign_keys="[EntityAbility.game_token, EntityAbility.entity_id]",
        cascade="all, delete-orphan",
        overlaps="entity,event")
    progress_records = db.relationship(
        'Progress',
        back_populates='host',
        foreign_keys="[Progress.game_token, Progress.host_id]",
        cascade="all, delete-orphan")

    __mapper_args__ = {
        'polymorphic_on': entity_type,
        'polymorphic_identity': TYPENAME
    }

# ------------------------------------------------------------------------
# Inherited Entities
# ------------------------------------------------------------------------

class Item(Entity):
    """The most important entity for idle scenarios,
    also generally flexible and multi-purpose.

    BLUEPRINT: Static definition of an item type (e.g., 'Iron Ore').
    Instance data (quantity, position) is managed by the Pile model.
    """
    TYPENAME = 'item'
    PLURAL = f'{TYPENAME}s'
    SHORT = TYPENAME
    __tablename__ = PLURAL

    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    storage_type = db.Column(
        db.String(1), nullable=False, default=StorageType.CARRIED)
    q_limit = db.Column(db.Float, default=0.0)
    slot_id = db.Column(db.Integer)
    loc_hosted = db.Column(db.Boolean, default=False)
    toplevel = db.Column(db.Boolean, default=False) # i.e. pinned
    masked = db.Column(db.Boolean, default=False)
    counted_for_unmasking = db.Column(db.Boolean, default=False)

    def to_dict(self):
        data = super().to_dict()
        data.update({
            "storage_type": self.storage_type,
            "q_limit": self.q_limit,
            "limits_for": sorted([l.to_dict() for l in self.limits_for], 
                                key=lambda x: x['owner_id']),
            "slot": self.slot_label,
            "loc_hosted": self.loc_hosted,
            "toplevel": self.toplevel,
            "masked": self.masked,
            "recipes": [
                r.to_dict() for r in
                sorted(self.recipes, key=lambda r: r.order_index)
            ],
        })
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token):
        updates = {
            'limits_for': [
                ItemLimit(game_token=game_token, **l_data)
                for l_data in data.pop('limits_for', [])
            ],
            'recipes': [
                Recipe.from_dict(r_data, game_token, order_index)
                for order_index, r_data in enumerate(data.pop('recipes', []))
            ],
        }
        slot_label = data.pop('slot', None)
        if slot_label:
            updates['slot_id'] = resolve_enum_id(
                game_token, EQUIPMENT_SLOTS_ID, slot_label)
        return super().from_dict(data, game_token, **updates)

    @property
    def slot_label(self):
        return self.slot_entry.label if self.slot_entry else ""

    def limit_for(self, owner_id):
        if not owner_id or owner_id == GENERAL_ID:
            return self.q_limit
        for limit in self.limits_for:
            if limit.owner.id == owner_id:
                return limit.q_limit
        return self.q_limit

    in_piles = db.relationship(
        'Pile',
        back_populates='item',
        foreign_keys="[Pile.game_token, Pile.item_id]",
        viewonly=True)
    limits_for = db.relationship(
        'ItemLimit',
        back_populates='item',
        cascade="all, delete-orphan")
    recipes = db.relationship(
        'Recipe', 
        back_populates='product', 
        foreign_keys="[Recipe.game_token, Recipe.product_id]",
        cascade="all, delete-orphan",
        order_by="Recipe.order_index")
    as_ingredient = db.relationship(
        'RecipeSource',
        back_populates='ingredient',
        foreign_keys="[RecipeSource.game_token, RecipeSource.item_id]",
        viewonly=True)
    as_byproducts = db.relationship(
        'RecipeByproduct',
        back_populates='item',
        foreign_keys="[RecipeByproduct.game_token, RecipeByproduct.item_id]",
        viewonly=True)
    slot_entry = db.relationship(
        'EnumEntry',
        foreign_keys=[slot_id],
        post_update=True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['slot_id'],
            ['enum_entries.id'], ondelete='SET NULL'),
        db.CheckConstraint(
            f"storage_type IN {StorageType.ALL_CODES}", 
            name="check_storage_type_valid"),
    )
    __mapper_args__ = {'polymorphic_identity': TYPENAME}

class Location(Entity):
    """Allows items or characters to be in different places,
    making the scenario bigger.

    SINGLETON: Each is a unique container for Piles and ItemRefs.
    """
    TYPENAME = 'location'
    PLURAL = f'{TYPENAME}s'
    SHORT = 'loc'
    __tablename__ = PLURAL

    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    toplevel = db.Column(db.Boolean, default=False)
    masked = db.Column(db.Boolean, default=False)
    dimensions = db.Column(ARRAY(db.Integer), default=None)

    def to_dict(self):
        data = super().to_dict()
        data.update({
            "dimensions": self.dimensions,
            "toplevel": self.toplevel,
            "masked": self.masked,
            "items": sorted(
                [p.to_dict() for p in self.piles], 
                key=lambda x: (x['item_id'], x.get('position') or [])),
            "item_refs": sorted(
                [ir.item_id for ir in self.item_refs]),
            "destinations": sorted(
                [d.to_dict() for d in self.routes_forward], 
                key=lambda x: x['loc2_id']),
            "entrance_reqs": sorted(
                [r.to_dict() for r in self.entrance_reqs], 
                key=lambda x: (x.get('item_id') or 0,
                            x.get('attrib_id') or 0)),
            "zones": [z.to_dict() for z in self.zones],
        })
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token):
        scrub_array(data, 'dimensions', 2)
        loc = super().from_dict(data, game_token)
        for i in data.get('items', []):
            loc.piles.append(
                Pile.from_dict(i, game_token, loc.id))
        for item_id in data.get('item_refs', []):
            new_ref = ItemRef(
                game_token=game_token,
                loc_id=loc.id,
                item_id=item_id
            )
            loc.item_refs.append(new_ref)
        for d in data.get('destinations', []):
            loc.routes_forward.append(
                LocDest.from_dict(d, game_token, loc.id))
        for r in data.get('entrance_reqs', []):
            loc.entrance_reqs.append(
                EntranceReq.from_dict(r, game_token, loc.id))
        for z in data.get('zones', []):
            loc.zones.append(
                LocZone.from_dict(z, game_token, loc.id))
        return loc

    @property
    def has_grid(self):
        return self.dimensions and self.dimensions[0] > 0

    @property
    def exits(self):
        forward = [
            r for r in self.routes_forward
            if r.direction in (DestExit.BOTH, DestExit.LOC1)]
        backward = [
            r for r in self.routes_backward
            if r.direction in (DestExit.BOTH, DestExit.LOC2)]
        return forward + backward

    item_refs = db.relationship(
        'ItemRef',
        back_populates='location',
        foreign_keys="[ItemRef.game_token, ItemRef.loc_id]",
        cascade="all, delete-orphan")
    characters_here = db.relationship(
        'Character',
        back_populates='location',
        foreign_keys="[Character.game_token, Character.location_id]")
    routes_forward = db.relationship(
        'LocDest',
        back_populates='loc1',
        foreign_keys="[LocDest.game_token, LocDest.loc1_id]",
        cascade="all, delete-orphan",
        overlaps="routes_backward")
    routes_backward = db.relationship(
        'LocDest',
        back_populates='loc2',
        foreign_keys="[LocDest.game_token, LocDest.loc2_id]",
        cascade="all, delete-orphan",
        overlaps="routes_forward")
    entrance_reqs = db.relationship(
        'EntranceReq',
        back_populates='location',
        cascade="all, delete-orphan",
        foreign_keys="[EntranceReq.game_token, EntranceReq.loc_id]")
    zones = db.relationship(
        'LocZone',
        back_populates='location',
        foreign_keys="[LocZone.game_token, LocZone.loc_id]",
        cascade="all, delete-orphan",
        order_by="LocZone.order_index")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
    )
    __mapper_args__ = {'polymorphic_identity': TYPENAME}

class Character(Entity):
    """The primary entity for role-playing scenarios.
    The location_id foreign key means that Location must be defined first.

    SINGLETON: Each is a unique actor and container for Piles.
    """
    TYPENAME = 'character'
    PLURAL = f'{TYPENAME}s'
    SHORT = 'char'
    __tablename__ = PLURAL

    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    toplevel = db.Column(db.Boolean, default=False)
    travel_party = db.Column(db.String(100))
    position = db.Column(ARRAY(db.Integer), default=None)
    location_id = db.Column(db.Integer)

    def to_dict(self):
        data = super().to_dict()
        data.update({
            "toplevel": self.toplevel,
            "location_id": self.location_id,
            "position": self.position,
            "travel_party": self.travel_party,
            "items": sorted([p.to_dict() for p in self.piles], 
                           key=lambda x: x['item_id']),
        })
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token):
        scrub_array(data, 'position', 2)
        char = super().from_dict(data, game_token)
        for i_data in data.get('items', []):
            char.piles.append(
                Pile.from_dict(i_data, game_token, char.id))
        return char

    location = db.relationship(
        'Location',
        back_populates='characters_here',
        foreign_keys=[game_token, location_id])

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'location_id'],
            ['locations.game_token', 'locations.id']),
    )
    __mapper_args__ = {'polymorphic_identity': TYPENAME}

class Attrib(Entity):
    """
    Functional or informational stats for other entities.
    Stats (Str), states (Is Open), or properties (Color).
    Can be numeric, boolean, or enumerated list.

    BLUEPRINT: Defines a property (e.g., 'Strength').
    Actual values for entities are stored in 'AttribVal' instances.
    """
    TYPENAME = 'attrib'
    PLURAL = f'{TYPENAME}s'
    SHORT = 'attr'
    __tablename__ = PLURAL

    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    is_binary = db.Column(db.Boolean, default=False)

    def to_dict(self):
        """Exports the attribute definition (type and states)."""
        data = super().to_dict()
        data.pop("attribs", None) 
        data.update({
            "is_binary": self.is_binary,
            "enum_list": [e.label for e in self.enum_entries]
        })
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token):
        attrib = super().from_dict(data, game_token)
        for idx, label in enumerate(data.get('enum_list', [])):
            attrib.enum_entries.append(EnumEntry(
                game_token=game_token,
                label=label,
                order_index=idx
            ))
        return attrib

    def id_to_rank(self, entry_id):
        """Return the 0-based order_index of the EnumEntry with the given id,
        or None.
        """
        for entry in self.enum_entries:
            if entry.id == int(entry_id):
                return entry.order_index
        return None

    def rank_to_id(self, rank):
        """Return the EnumEntry.id whose order_index equals rank, or None."""
        for entry in self.enum_entries:
            if entry.order_index == rank:
                return entry.id
        return None

    def format_value(self, val, show_rank=False):
        """
        Converts a raw float value into a string based on this 
        attribute's definition.
        """
        if self.is_binary:
            label = "✓" if val > 0 else "✗"
            if show_rank:
                return f"{val:g} ({label})"
            return label
        
        if self.enum_entries:
            try:
                entry_id = int(val)
                for entry in self.enum_entries:
                    if entry.id == entry_id:
                        if show_rank:
                            return f"{entry.label} ({entry.order_index})"
                        return entry.label
            except (ValueError, TypeError):
                pass
            return "?"

        from .utils import format_num
        return format_num(val)

    attrib_values = db.relationship(
        'AttribVal',
        back_populates='attrib',
        foreign_keys="[AttribVal.game_token, AttribVal.attrib_id]",
        viewonly=True)
    reqs = db.relationship(
        'RecipeAttribReq',
        back_populates='attrib',
        foreign_keys="[RecipeAttribReq.game_token, RecipeAttribReq.attrib_id]",
        viewonly=True)
    enum_entries = db.relationship(
        'EnumEntry',
        back_populates='attrib',
        cascade="all, delete-orphan",
        order_by="EnumEntry.order_index",
        foreign_keys="[EnumEntry.game_token, EnumEntry.attrib_id]")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['id', 'game_token'],
            ['entities.id', 'entities.game_token'], ondelete='CASCADE'),
    )
    __mapper_args__ = {'polymorphic_identity': TYPENAME}


class OutcomeType:
    FOURWAY ='fourway'        # strong/weak success or failure
    NUMERIC = 'numeric'       # such as a damage number
    DETERMINED = 'determined' # calculate from a single base number
    SELECT = 'selection'      # random selection from a list
    COORDS = 'coordinates'    # random coordinates from a location's grid
    ROLLER = 'dice_system'    # manual inputs for standardized system

    ALL = [FOURWAY, NUMERIC, DETERMINED, SELECT, COORDS, ROLLER]

class SuccessTier:
    ALWAYS = 'always'
    SUCCESS_ANY = 'success_any'
    SUCCESS_NAT_MAX = 'natural_max'
    SUCCESS_MAJOR = 'success_major'
    SUCCESS_MINOR = 'success_minor'
    FAILURE_ANY = 'failure_any'
    FAILURE_NAT_MIN = 'natural_min'
    FAILURE_MAJOR = 'failure_major'
    FAILURE_MINOR = 'failure_minor'

class RollerType:
    """System for ROLLER outcome type"""
    DND = 'dnd'
    IRONSWORN = 'ironsworn'

    ALL = [DND, IRONSWORN]

class Event(Entity):
    """Actions and things that can happen, typically with chance.
    Many events use or change the Attrib values of other entities.
    """
    TYPENAME = 'event'
    PLURAL = f'{TYPENAME}s'
    SHORT = 'event'
    __tablename__ = PLURAL

    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    toplevel = db.Column(db.Boolean, default=False)
    outcome_type = db.Column(
        db.String(20), nullable=False, default=OutcomeType.FOURWAY)
    roller_type = db.Column(db.String(20))
    numeric_range = db.Column(ARRAY(db.Integer)) # [min, max]
    fixed_base = db.Column(db.Float, default=0.0)
    selection_attrib_id = db.Column(db.Integer)

    def to_dict(self):
        data = super().to_dict()
        data.update({
            "toplevel": self.toplevel,
            "outcome_type": self.outcome_type,
            "roller_type": self.roller_type \
                if self.outcome_type == OutcomeType.ROLLER else None,
            "numeric_range": self.numeric_range \
                if self.outcome_type in (
                OutcomeType.FOURWAY, OutcomeType.NUMERIC) else None,
            "fixed_base": self.fixed_base \
                if self.outcome_type == OutcomeType.DETERMINED else None,
            "selection_attrib_id": self.selection_attrib_id
                if self.outcome_type == OutcomeType.SELECT else None,
            "determinants": [d.to_dict() for d in self.determinants],
            "effects": [e.to_dict() for e in self.effects],
            "chained": sorted([l.to_dict() for l in self.chained], 
                             key=lambda x: x['child_id']),
        })
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token):
        event = super().from_dict(data, game_token)
        dets = data.get('determinants', [])
        effects = data.get('effects', [])
        event.factors = [
            EventFactor.from_dict(
                d, game_token, event.id,
                usage_type=Participant.DET, order_index=idx) 
            for idx, d in enumerate(dets)
        ] + [
            EventFactor.from_dict(
                e, game_token, event.id,
                usage_type=Participant.EFF, order_index=idx) 
            for idx, e in enumerate(effects)
        ]
        for child_data in data.get('chained', []):
            event.chained.append(
                EventLink.from_dict(child_data, game_token, event.id))
        return event

    @property
    def determinants(self):
        return sorted(
            [f for f in self.factors if f.usage_type == Participant.DET],
            key=lambda f: f.order_index or 0
        )

    @property
    def effects(self):
        return sorted(
            [f for f in self.factors if f.usage_type == Participant.EFF],
            key=lambda f: f.order_index or 0
        )

    factors = db.relationship(
        'EventFactor',
        back_populates='event',
        cascade="all, delete-orphan")
    chained = db.relationship(
        'EventLink',
        back_populates='parent',
        foreign_keys="[EventLink.game_token, EventLink.parent_id]",
        cascade="all, delete-orphan")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
        db.CheckConstraint(
            outcome_type.in_(OutcomeType.ALL), name='check_outcome_type_valid'),
        db.CheckConstraint(
            roller_type.in_(RollerType.ALL), name='check_roller_type_valid'),
    )
    __mapper_args__ = {'polymorphic_identity': TYPENAME}

ENTITIES = {
    'attribs': Attrib,
    'items': Item,
    'locations': Location,
    'characters': Character,
    'events': Event
}

# ------------------------------------------------------------------------
# Association Tables (Many-to-Many)
# ------------------------------------------------------------------------

class Pile(db.Model, DictHydrator):
    """Consolidated pile of items. The owner defines whether it is:
    * held by a Character
    * or at a Location
    * or neither; the general pile of that Item
    """
    __tablename__ = 'piles'
    # Single Primary Key: Postgres handles autoincrement globally without drama
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # Still important for foreign keys
    game_token = db.Column(db.String(50), index=True, nullable=False)
    owner_id = db.Column(db.Integer, nullable=False)
    item_id = db.Column(db.Integer, nullable=False)
    # Not relevant for universal storage
    position = db.Column(ARRAY(db.Integer), default=None)
    quantity = db.Column(db.Float, nullable=False, default=0.0)
    slot_id = db.Column(db.Integer)

    @property
    def slot_label(self):
        return self.slot_entry.label if self.slot_entry else ""

    def to_dict(self):
        data = {
            "item_id": self.item_id,
            "quantity": self.quantity,
            "position": self.position,
            "slot": self.slot_label
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, owner_id):
        scrub_array(data, 'position', 2)
        updates = {}
        slot_label = data.pop('slot', None)
        if slot_label:
            updates['slot_id'] = resolve_enum_id(
                game_token, EQUIPMENT_SLOTS_ID, slot_label)
        return super().from_dict(
            data, game_token, owner_id=owner_id, **updates)

    @property
    def is_placed(self):
        """The item stack occupies a grid coordinate on the floor
        at a location.

        This isn't true if the item stack is at a location without
        a defined grid, or with an owner, or in universal storage.
        """
        return self.position is not None

    def merge_to(self, target_position):
        """
        Moves this pile to a new position. If a pile of the same item 
        already exists there, merges this one into it.
        """
        if not isinstance(target_position, (list, tuple)):
            target_position = list(target_position)

        # 1. Look for an existing pile at the target
        existing_pile = Pile.query.filter_by(
            game_token=self.game_token,
            owner_id=self.owner_id,
            item_id=self.item_id,
            position=target_position
        ).first()

        if existing_pile and existing_pile.id != self.id:
            # 2. Merge quantities
            existing_pile.quantity += self.quantity
            # 3. Remove the current (source) pile from the session
            db.session.delete(self)
            return existing_pile
        else:
            # 4. No collision, just move this pile
            self.position = target_position
            return self

    def __repr__(self):
        pos_label = f"at {self.position}" if self.is_placed else "unplaced"
        return (
            f"<Pile {self.item_id} (qty {self.quantity})"
            f" {pos_label} owner {self.owner_id}>")

    owner = db.relationship(
        'Entity',
        back_populates='piles',
        foreign_keys=[game_token, owner_id],
        overlaps="item")
    item = db.relationship(
        'Item',
        back_populates='in_piles',
        foreign_keys=[game_token, item_id],
        overlaps="owner,piles")
    slot_entry = db.relationship(
        'EnumEntry',
        foreign_keys=[slot_id],
        post_update=True)

    __table_args__ = (
        # Ensure uniqueness depending on whether position is NULL
        db.Index('idx_pile_unpositioned_unique', 
              'game_token', 'owner_id', 'item_id',
              unique=True,
              postgresql_where=(db.column('position').is_(None))),
        db.Index('idx_pile_positioned_unique', 
              'game_token', 'owner_id', 'item_id', 'position',
              unique=True,
              postgresql_where=(db.column('position').is_not(None))),
        # Define foreign keys in the "child" side of relationships
        db.ForeignKeyConstraint(
            ['game_token', 'owner_id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'item_id'],
            ['items.game_token', 'items.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['slot_id'],
            ['enum_entries.id'], ondelete='SET NULL'),
    )

class ItemLimit(db.Model, DictHydrator):
    """Overrides the default item q_limit for a specific owner."""
    __tablename__ = 'item_limits'
    game_token = db.Column(db.String(50), primary_key=True)
    item_id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, primary_key=True)
    q_limit = db.Column(db.Float, nullable=False, default=0.0)

    def to_dict(self):
        return {
            "owner_id": self.owner_id,
            "q_limit": self.q_limit
        }

    item = db.relationship(
        'Item', 
        back_populates='limits_for',
        foreign_keys=[game_token, item_id])
    owner = db.relationship(
        'Entity',
        foreign_keys=[game_token, owner_id],
        overlaps="item,limits_for")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'item_id'],
            ['items.game_token', 'items.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'owner_id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
    )

class AttribVal(db.Model):
    """Values of an Attrib applied to an Entity (Item, Character, or Location)."""
    __tablename__ = 'attrib_values'
    game_token = db.Column(db.String(50), primary_key=True)
    attrib_id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.Float, nullable=False, default=0.0)

    @property
    def serialized_value(self):
        return attrib_val_to_json(self.game_token, self.attrib_id, self.value)

    @property
    def display(self):
        return self.attrib.format_value(self.value)

    subject = db.relationship(
        'Entity',
        back_populates='attrib_values',
        foreign_keys=[game_token, subject_id],
        overlaps="attrib,attrib_values")
    attrib = db.relationship(
        'Attrib',
        back_populates='attrib_values',
        foreign_keys=[game_token, attrib_id],
        overlaps="subject,attrib_values")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'attrib_id'],
            ['attribs.game_token', 'attribs.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'subject_id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
    )

class EnumEntry(db.Model, DictHydrator):
    """Labels for an Attrib's enumerated states."""
    __tablename__ = 'enum_entries'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    attrib_id = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(255), nullable=False)
    order_index = db.Column(db.Integer, default=0)

    attrib = db.relationship(
        'Attrib', 
        back_populates='enum_entries',
        foreign_keys=[game_token, attrib_id])

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'attrib_id'],
            ['attribs.game_token', 'attribs.id'], ondelete='CASCADE'),
    )

class EntranceReq(db.Model, DictHydrator):
    """Requirements that must be met for a char to arrive at this location."""
    __tablename__ = 'entrance_reqs'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    loc_id = db.Column(db.Integer, nullable=False)
    
    item_id = db.Column(db.Integer)
    attrib_id = db.Column(db.Integer)
    val_required = db.Column(db.Float)

    def to_dict(self):
        data = {
            "item_id": self.item_id,
            "attrib_id": self.attrib_id,
            "val_required": attrib_val_to_json(
                self.game_token, self.attrib_id, self.val_required)
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, loc_id):
        updates = {}
        attrib_id = data.get('attrib_id')
        if attrib_id:
            updates['val_required'] = attrib_val_from_json(
                game_token, attrib_id, data.pop('val_required', 0.0))
        return super().from_dict(data, game_token, loc_id=loc_id, **updates)

    location = db.relationship(
        'Location',
        back_populates='entrance_reqs',
        foreign_keys=[game_token, loc_id])
    item = db.relationship(
        'Item',
        foreign_keys=[game_token, item_id])
    attrib = db.relationship(
        'Attrib',
        foreign_keys=[game_token, attrib_id])

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'loc_id'],
            ['locations.game_token', 'locations.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'item_id'],
            ['items.game_token', 'items.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'attrib_id'],
            ['attribs.game_token', 'attribs.id'], ondelete='CASCADE'),
    )


class DestExit:
    BOTH = 'both'
    LOC1 = 'loc1'  # loc1 to loc2 only
    LOC2 = 'loc2'  # loc2 to loc1 only

    ALL = [BOTH, LOC1, LOC2]

class LocDest(db.Model, DictHydrator):
    __tablename__ = 'loc_destinations'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    loc1_id = db.Column(db.Integer, nullable=False)
    loc2_id = db.Column(db.Integer, nullable=False)
    door1 = db.Column(ARRAY(db.Integer))
    door2 = db.Column(ARRAY(db.Integer))
    direction = db.Column(db.String(4), default=DestExit.BOTH)

    def to_dict(self):
        data = {
            "loc2_id": self.loc2_id,
            "door1": self.door1,
            "door2": self.door2,
            "direction": self.direction
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, loc1_id):
        scrub_array(data, 'door1', 2)
        scrub_array(data, 'door2', 2)
        return super().from_dict(data, game_token, loc1_id=loc1_id)

    def other_loc(self, loc_id):
        if self.loc1_id == loc_id:
            return self.loc2
        if self.loc2_id == loc_id:
            return self.loc1
        return None

    def door_at(self, loc_id):
        if self.loc1_id == loc_id:
            return self.door1
        if self.loc2_id == loc_id:
            return self.door2
        return None

    loc1 = db.relationship(
        'Location',
        back_populates='routes_forward',
        foreign_keys=[game_token, loc1_id],
        overlaps="loc2,routes_backward")
    loc2 = db.relationship(
        'Location',
        back_populates='routes_backward',
        foreign_keys=[game_token, loc2_id],
        overlaps="loc1,routes_forward")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'loc1_id'],
            ['locations.game_token', 'locations.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'loc2_id'],
            ['locations.game_token', 'locations.id'], ondelete='CASCADE'),
        db.CheckConstraint(
            direction.in_(DestExit.ALL), name='check_direction_valid'),
    )

class LocZone(db.Model, DictHydrator):
    __tablename__ = 'loc_zones'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    loc_id = db.Column(db.Integer, nullable=False)
    coords = db.Column(ARRAY(db.Integer), nullable=False) # l,t,r,b
    
    label = db.Column(db.String(100))
    color = db.Column(db.String(20)) # e.g. "rgba(255,0,0,0.2)" or "#330000"
    prevents_travel = db.Column(db.Boolean, default=True)
    order_index = db.Column(db.Integer, default=0)

    def to_dict(self):
        data = {
            "coords": self.coords,
            "label": self.label,
            "color": self.color,
            "prevents_travel": self.prevents_travel
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, loc_id):
        scrub_array(data, 'coords', 4)
        return super().from_dict(data, game_token, loc_id=loc_id)

    location = db.relationship(
        'Location',
        back_populates='zones',
        foreign_keys=[game_token, loc_id])

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'loc_id'],
            ['locations.game_token', 'locations.id'], ondelete='CASCADE'),
    )

class ItemRef(db.Model):
    """Reference to an item shown when viewing that host.
    Such items aren't actually anchored there, only mentioned.
    This is a good way to organize a set of related universal items.
    """
    __tablename__ = 'item_refs'
    game_token = db.Column(db.String(50), primary_key=True)
    loc_id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, primary_key=True)

    location = db.relationship(
        'Location',
        back_populates='item_refs',
        foreign_keys=[game_token, loc_id])
    item = db.relationship(
        'Item',
        foreign_keys=[game_token, item_id])

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'loc_id'],
            ['locations.game_token', 'locations.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'item_id'],
            ['items.game_token', 'items.id'], ondelete='CASCADE'),
    )

# ------------------------------------------------------------------------
# Events
# ------------------------------------------------------------------------

class Participant:
    """References a field to fetch or target for an event factor."""

    # --- Context-Driven Roles of the Anchor Entity ---
    SUBJECT = 'subject'     # Entity that triggered the event
    TARGET = 'target char'  # Nearby or explicitly chosen other char
    AT = 'at'               # Current location
    OWNER = 'owner'         # Entity holding the item
    UNIVERSAL = 'universal' # Global items
    BLUEPRINT = 'blueprint' # Item blueprint (not pile) e.g. Recipes
    CONTEXT_ROLES = [SUBJECT, TARGET, AT, OWNER, UNIVERSAL, BLUEPRINT]

    ROLE_SUFFIX = '_role_id'

    @staticmethod
    def formkey_to_role(key):
        if key and key.endswith(Participant.ROLE_SUFFIX):
            return key[:-len(Participant.ROLE_SUFFIX)]
        return key

    @staticmethod
    def role_to_formkey(role):
        if role:
            if role.endswith(Participant.ROLE_SUFFIX):
                return role
            return f"{role}{Participant.ROLE_SUFFIX}"
        return role

    # --- Depth Traversal ---
    # False: Use the anchor entity itself.
    # True: Select an item pile inside the anchor.
    # Only valid if the anchor resolves to a Character or Location.
    ChildItem = False

    # --- Get Value From ---
    INFIELD = 'infield'   # Standard EventField setup
    OUTFIELD = 'outfield' # Treat infield and outfield the same
    OUTCOME = 'outcome'   # Read the roll result
    CONST = 'const'       # Treat val_transform as the input

    # --- Field Mode ---
    ATTR = 'attr'   # AttribVal
    QTY  = 'qty'    # Pile quantity
    LIMIT = 'limit' # Pile default limit
    RATE_AMT = 'rate_amt' # Recipe.rate_amount
    RATE_DUR = 'rate_dur' # Recipe.rate_duration
    SOURCE_QTY = 'src_qty' # Recipe quantity for a particular source
    BYP_QTY = 'byp_qty'    # Recipe quantity for a particular byproduct
    DIST = 'dist'   # Distance from subject grid pos (read-only)
    PLACE = 'place' # Create pile at position (write-only)
    POS = 'pos'     # Teleport char to location and position
    SPAWN = 'spawn' # Create numbered duplicate of char

    ALL_MODES = [
        ATTR, QTY, LIMIT, RATE_AMT, RATE_DUR, SOURCE_QTY, BYP_QTY,
        DIST, PLACE, POS, SPAWN]
    USES_ATTRIB = {ATTR}
    USES_ITEM = {QTY, LIMIT, RATE_AMT, RATE_DUR, SOURCE_QTY, BYP_QTY, PLACE}
    USES_RECIPE = {RATE_AMT, RATE_DUR, SOURCE_QTY, BYP_QTY}
    USES_BLUEPRINT = {
        LIMIT, RATE_AMT, RATE_DUR, SOURCE_QTY, BYP_QTY, PLACE, SPAWN}
    USES_SOURCE_ITEM = {SOURCE_QTY, BYP_QTY}
    USES_LOC = {POS, SPAWN, PLACE}
    USES_CHAR = {SPAWN}

    # --- Usage ---
    DET = 'det'  # Determinant (affects roll)
    EFF = 'eff'  # Effect (applies changes)
    CHAIN = 'chn' # Criteria for EventLink
    ALL_USAGE = [DET, EFF, CHAIN]

class Operation:
    CONST = 'c'
    EQ = '=='
    GE = '>=' # often means having enough
    LT = '<' # often means NOT having that amount
    NE = '!=' # value must exist and be different (unlike EventFactor.negate)
    ASSIGN = ':='
    ADD = '+'
    SUB = '-'
    MULT = '*'
    DIV = '/'
    MOD = '%'
    VAL_TO_POW = 'x^'
    POW_OF_VAL = '^x'
    ABS = 'abs'
    ROUND = 'round'
    FLOOR = 'floor'
    CEIL = 'ceil'
    MIN = 'min'
    MAX = 'max'
    SOFTCAP = 'scap'
    MEM_STORE = 'm:='
    MEM_RECALL = 'mr'

    Repr = {
        CONST:      'n',
        EQ:         '=',
        GE:         '≥',
        LT:         '<',
        NE:         '≠',
        ASSIGN:     '→',
        ADD:        '+',
        SUB:        '−',
        MULT:       '×',
        DIV:        '÷',
        MOD:        'Mod',
        VAL_TO_POW: 'xⁿ',
        POW_OF_VAL: 'nˣ',
        ABS:        'Abs',
        ROUND:      'Round',
        FLOOR:      'Floor',
        CEIL:       'Ceiling',
        MIN:        'Min',
        MAX:        'Max',
        SOFTCAP:    'SoftCap',
        MEM_STORE:  'Store',
        MEM_RECALL: 'Recall',
    }

    # How the result applies to the total
    DET_APPLICATION = [
        ADD, SUB, MULT, DIV, MOD, ASSIGN, MEM_STORE, EQ, GE, LT, NE]
    COMPARISON = [EQ, GE, LT, NE]

    # Modify the Field Value before we apply it to the total
    TRANSFORM = [
        ADD, SUB, MULT, DIV, MOD,
        VAL_TO_POW, POW_OF_VAL, ROUND, FLOOR, CEIL, ABS, MIN, MAX, SOFTCAP]
    FUNCTIONAL = [ROUND, FLOOR, CEIL, MIN, MAX, SOFTCAP]


class EventField(db.Model, DictHydrator):
    """
    Access an attribute or item quantity by event role.

    Attribute Value Lookup:
        - Look up an AttribVal for the given attrib_id, either for an
          anchor or one of their child items.
        - Example 1: Role SUBJECT, Mode ATTR, Attrib STR, AttribVal 3 
        - Example 2: Role SUBJECT, ChildItem True, Mode ATTR, Attrib ACCURACY,
            AttribVal 4 (a pile selected from the list of subject's inventory
            and nearby carried/local piles)

    Item Quantity Lookup:
        - Look up the quantity of a Pile for the given item_id,
          either specified in configuration or chosen by dropdown in play.
        - Example: Role SUBJECT_ITEM_QTY, Item LEATHER (How much leather Bob has)
    """
    __tablename__ = 'event_fields'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)

    role = db.Column(db.String(20), nullable=False, default=Participant.SUBJECT)
    field_mode = db.Column(db.String(10), nullable=False, default=Participant.ATTR)
    child_of_anchor = db.Column(db.Boolean, nullable=False, default=False)

    item_id = db.Column(db.Integer, nullable=True)
    attrib_id = db.Column(db.Integer, nullable=True)
    char_id = db.Column(db.Integer, nullable=True)
    loc_id = db.Column(db.Integer, nullable=True)
    recipe_id = db.Column(db.Integer, nullable=True)
    source_item_id = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        data = {
            "role": self.role,
            "field_mode": self.field_mode,
            "child_of_anchor": self.child_of_anchor,
            "item_id": self.item_id,
            "attrib_id": self.attrib_id,
            "char_id": self.char_id,
            "loc_id": self.loc_id,
            "recipe_id": self.recipe_id,
            "source_item_id": self.source_item_id,
        }
        return self.to_dict_sparse(data)

    def get_field_name(self):
        from .utils import maskable_name
        if self.field_mode == Participant.ATTR and self.attrib_id:
            attrib = db.session.get(Attrib, (self.game_token, self.attrib_id))
            if not attrib:
                return f"(!broken attrib!)"
            if self.item_id:
                item = db.session.get(Item, (self.game_token, self.item_id))
                item_label = f" of {maskable_name(item)}" if item else ""
            else:
                item_label = ""
            inventory_item = "Item " if self.child_of_anchor else ""
            return f"{inventory_item}{attrib.name}{item_label}"
        if self.item_id:
            item = db.session.get(Item, (self.game_token, self.item_id))
            if self.field_mode == Participant.QTY:
                return f"{maskable_name(item)} Qty"
            if self.field_mode == Participant.LIMIT:
                return f"{maskable_name(item)} Limit"
            if self.field_mode == Participant.DIST:
               return f"Distance from Subject"
            if self.field_mode == Participant.RATE_AMT and self.recipe_id:
                return f"{maskable_name(item)} Yield"
            if self.field_mode == Participant.RATE_DUR and self.recipe_id:
                return f"{maskable_name(item)} Recipe Duration"
            if self.field_mode == Participant.PLACE:
                return f"Place {maskable_name(item)}"
            if self.field_mode in (
                    Participant.SOURCE_QTY, Participant.BYP_QTY) \
                    and self.recipe_id and self.source_item_id:
                src = db.session.get(
                    Item, (self.game_token, self.source_item_id))
                label = "Source" if self.field_mode == Participant.SOURCE_QTY \
                    else "Byproduct"
                return f"{maskable_name(item)} {label}" \
                       f" ({maskable_name(src)}) Qty"
        return ""

    __table_args__ = (
        db.CheckConstraint(
            "length(role) > 0", name='check_role_valid'),
        db.CheckConstraint(
            field_mode.in_(Participant.ALL_MODES), name='check_field_valid'),
    )

class EventFactor(db.Model, DictHydrator):
    """
    Combine a retrieved value into a Determinant or Effect.
    Defines how to retrieve the value and how to apply it.
    """
    __tablename__ = 'event_factors'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    event_id = db.Column(db.Integer, nullable=False)

    # --- Identity & Logic Type ---
    usage_type = db.Column(db.String(3), nullable=False, default=Participant.DET)
    order_index = db.Column(db.Integer, default=0)
    label = db.Column(db.String(50))

    # --- Filter & Workflow ---
    negate = db.Column(db.Boolean, default=False) # = not(lookup && compare)
    outcome_success = db.Column(db.String(20), default=SuccessTier.ALWAYS)
    auto_apply = db.Column(db.Boolean, default=False)

    # --- Retrieval ---
    get_val_from = db.Column(db.String(15), nullable=False, default=Participant.INFIELD)
    infield_id = db.Column(db.Integer, nullable=True)
    outfield_id = db.Column(db.Integer, nullable=True) # Effects only

    # --- Formula That Applies The Values ---
    #
    # Total <op_application> (RetrievedVal <op_transform> val_transform)
    #
    # Examples:
    #   * (1.5 ^ Qty)      -> op_app: MULT, op_trans: POW_OF_VAL, val_trans: 1.5
    #   (Str - 1) == 10    -> op_app: EQ, op_trans: SUB, val_trans: 1.0, val_req: 10
    #   + log(HP)          -> op_app: ADD, scaling: 'log'
    #   
    op_application = db.Column(db.String(5), default=Operation.ADD) # outer op
    op_transform = db.Column(db.String(5), default=None) # inner op
    val_transform = db.Column(db.Float, default=1.0) # inner constant
    val_required = db.Column(db.Float, default=1.0) # comparison RHS

    def to_dict(self):
        attrib_id = (self.infield.attrib_id if self.infield else None) or \
                    (self.outfield.attrib_id if self.outfield else None)
        data = {
            "label": self.label,
            "get_val_from": self.get_val_from,
            "infield": self.infield.to_dict() if self.infield else None,
            "outfield": self.outfield.to_dict() if self.outfield else None,
            "op_application": self.op_application,
            "op_transform": self.op_transform,
            "val_transform": self.val_transform,
            "val_required": attrib_val_to_json(
                self.game_token, attrib_id, self.val_required
                ) if self.is_comparison else None,
            "negate": self.negate,
            "outcome_success": self.outcome_success,
            "auto_apply": self.auto_apply
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, event_id, **overrides):
        updates = {}
        in_data = data.get('infield', {})
        out_data = data.get('outfield', {})
        attrib_id = in_data.get('attrib_id') or out_data.get('attrib_id')
        if attrib_id:
            if 'val_required' in data:
                updates['val_required'] = attrib_val_from_json(
                    game_token, attrib_id, data.pop('val_required'))
        if in_data:
            updates['infield'] = EventField(game_token=game_token, **in_data)
        if out_data:
            updates['outfield'] = EventField(game_token=game_token, **out_data)
        
        updates.update(overrides)
        return super().from_dict(
            data, game_token, event_id=event_id, **updates)

    @property
    def role(self):
        return self.infield.role if self.infield else None

    @property
    def is_comparison(self):
        return self.op_application in Operation.COMPARISON

    @property
    def op_app_display(self):
        if not self.op_application:
            return ""
        return Operation.Repr.get(self.op_application, self.op_application)

    @property
    def op_inner_display(self):
        if not self.op_transform:
            return ""
        return Operation.Repr.get(self.op_transform, self.op_transform)

    @property
    def val_required_display(self):
        field = self.infield or self.outfield
        if not field:
            return str(self.val_required)
        return attrib_val_to_json(
            self.game_token, field.attrib_id, self.val_required)

    # Relationships
    event = db.relationship(
        'Event',
        back_populates='factors',
        foreign_keys=[game_token, event_id])
    infield = db.relationship(
        'EventField',
        foreign_keys=[infield_id],
        cascade="all, delete-orphan", single_parent=True)
    outfield = db.relationship(
        'EventField',
        foreign_keys=[outfield_id],
        cascade="all, delete-orphan", single_parent=True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'event_id'],
            ['events.game_token', 'events.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['infield_id'], 
            ['event_fields.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['outfield_id'], 
            ['event_fields.id'], ondelete='CASCADE'),
        db.CheckConstraint(
            usage_type.in_(Participant.ALL_USAGE), name='check_usage_type_valid'),
    )

class EventLink(db.Model, DictHydrator):
    """Connections between events to create chains/sequences."""
    __tablename__ = 'event_links'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    
    parent_id = db.Column(db.Integer, nullable=False)
    child_id = db.Column(db.Integer, nullable=False)

    factor_id = db.Column(db.Integer, nullable=True)
    
    def to_dict(self):
        data = {
            "child_id": self.child_id,
            "req": self.req.to_dict() if self.req else None
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, parent_id):
        link = super().from_dict(data, game_token, parent_id=parent_id)
        req_data = data.get('req')
        if req_data:
            link.req = EventFactor.from_dict(
                req_data, game_token, parent_id, usage_type=Participant.CHAIN)
        return link

    parent = db.relationship(
        'Event',
        foreign_keys=[game_token, parent_id],
        back_populates='chained')
    child = db.relationship(
        'Event',
        foreign_keys=[game_token, child_id],
        viewonly=True)
    req = db.relationship(
        'EventFactor',
        foreign_keys=[factor_id],
        cascade="all, delete-orphan", 
        single_parent=True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'parent_id'],
            ['events.game_token', 'events.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'child_id'],
            ['events.game_token', 'events.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['factor_id'], 
            ['event_factors.id'], ondelete='CASCADE'),
    )

class EntityAbility(db.Model):
    """Who can call events, for example, a specific character. Shows a link
    to the event that sets the caller as the subject.
    """
    __tablename__ = 'entity_abilities'
    game_token = db.Column(db.String(50), primary_key=True)
    entity_id = db.Column(db.Integer, primary_key=True) # The Caller (Char/Loc/Item)
    event_id = db.Column(db.Integer, primary_key=True)  # The Event being called

    entity = db.relationship(
        'Entity',
        foreign_keys=[game_token, entity_id],
        back_populates='_ability_links',
        overlaps="_ability_links,event")
    event = db.relationship(
        'Event',
        foreign_keys=[game_token, event_id],
        viewonly=True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'entity_id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'event_id'],
            ['events.game_token', 'events.id'], ondelete='CASCADE'),
    )

# ------------------------------------------------------------------------
# Recipes
# ------------------------------------------------------------------------

class Recipe(db.Model, DictHydrator):
    __tablename__ = 'recipes'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, nullable=False)
    order_index = db.Column(db.Integer, default=0)
    rate_amount = db.Column(db.Float, default=1.0)
    rate_duration = db.Column(db.Integer, default=3)
    instant = db.Column(db.Boolean, default=False)

    def to_dict(self):
        data = {
            "id": self.id,
            "rate_amount": self.rate_amount,
            "rate_duration": self.rate_duration,
            "instant": self.instant,
            "sources": sorted(
                [s.to_dict() for s in self.sources],
                key=lambda x: x['item_id']),
            "byproducts": sorted(
                [b.to_dict() for b in self.byproducts],
                key=lambda x: x['item_id']),
            "attrib_reqs": sorted(
                [ar.to_dict() for ar in self.attrib_reqs],
                key=lambda x: x['attrib_id']),
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, order_index):
        recipe = super().from_dict(
            data, game_token, order_index=order_index)
        recipe.sources = [
            RecipeSource(game_token=game_token, **s)
            for s in data.get('sources', [])]
        recipe.byproducts = [
            RecipeByproduct(game_token=game_token, **b)
            for b in data.get('byproducts', [])]
        recipe.attrib_reqs = [
            RecipeAttribReq.from_dict(ar, game_token, recipe_id=recipe.id) 
            for ar in data.get('attrib_reqs', [])
        ]
        return recipe

    @property
    def source_items(self):
        return [src.ingredient for src in self.sources]

    @property
    def is_location_hosted(self):
        return any(
            s.ingredient.storage_type == StorageType.LOCAL and 
            s.ingredient.loc_hosted 
            for s in self.sources
        )

    @property
    def net_product_change(self):
        """Calculates Yield minus Consumption for the primary product."""
        net = self.rate_amount
        for s in self.sources:
            if s.item_id == self.product_id and not s.preserve:
                net -= s.q_required
        return net

    @property
    def is_producer(self):
        return self.net_product_change > 0

    @property
    def is_consumer(self):
        return self.net_product_change < 0

    @property
    def summary(self):
        """Generates a string like: 'Yield 1 from Iron, Coal'"""
        from app.utils import maskable_name
        sources = ", ".join([
            maskable_name(s.ingredient) for s in self.sources[:3]
        ])
        if not sources:
            return f"Yield {self.rate_amount:g} (No ingredients)"
        return f"Yield {self.rate_amount:g} from {sources}"

    product = db.relationship(
        'Item',
        back_populates='recipes',
        foreign_keys=[game_token, product_id])
    sources = db.relationship(
        'RecipeSource', 
        back_populates='recipe',
        foreign_keys="[RecipeSource.game_token, RecipeSource.recipe_id]",
        cascade="all, delete-orphan")
    byproducts = db.relationship(
        'RecipeByproduct',
        back_populates='recipe',
        foreign_keys="[RecipeByproduct.game_token, RecipeByproduct.recipe_id]",
        cascade="all, delete-orphan")
    attrib_reqs = db.relationship(
        'RecipeAttribReq',
        back_populates='recipe',
        foreign_keys="[RecipeAttribReq.game_token, RecipeAttribReq.recipe_id]",
        cascade="all, delete-orphan")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'product_id'],
            ['items.game_token', 'items.id'], ondelete='CASCADE'),
    )

class RecipeSource(db.Model, DictHydrator):
    __tablename__ = 'recipe_sources'
    game_token = db.Column(db.String(50), primary_key=True)
    recipe_id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, primary_key=True) # Ingredient
    q_required = db.Column(db.Float, nullable=False)
    preserve = db.Column(db.Boolean, default=False)

    def to_dict(self):
        data = {
            "item_id": self.item_id,
            "q_required": self.q_required,
            "preserve": self.preserve
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, recipe_id):
        return super().from_dict(data, game_token, recipe_id=recipe_id)

    recipe = db.relationship(
        'Recipe',
        back_populates='sources',
        foreign_keys=[game_token, recipe_id],
        overlaps="ingredient,as_ingredient")
    ingredient = db.relationship(
        'Item',
        back_populates='as_ingredient',
        foreign_keys=[game_token, item_id],
        overlaps="recipe,sources")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'recipe_id'],
            ['recipes.game_token', 'recipes.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'item_id'],
            ['items.game_token', 'items.id'], ondelete='CASCADE'),
    )

class RecipeByproduct(db.Model, DictHydrator):
    __tablename__ = 'recipe_byproducts'
    game_token = db.Column(db.String(50), primary_key=True)
    recipe_id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, primary_key=True)
    rate_amount = db.Column(db.Float, nullable=False)

    def to_dict(self):
        data = {
            "item_id": self.item_id,
            "rate_amount": self.rate_amount
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, recipe_id):
        return super().from_dict(data, game_token, recipe_id=recipe_id)

    recipe = db.relationship(
        'Recipe',
        back_populates='byproducts',
        foreign_keys=[game_token, recipe_id],
        overlaps="item,as_byproducts")
    item = db.relationship(
        'Item',
        back_populates='as_byproducts',
        foreign_keys=[game_token, item_id],
        overlaps="recipe,byproducts")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'recipe_id'],
            ['recipes.game_token', 'recipes.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'item_id'],
            ['items.game_token', 'items.id'], ondelete='CASCADE'),
    )

class RecipeAttribReq(db.Model, DictHydrator):
    __tablename__ = 'recipe_attrib_reqs'
    game_token = db.Column(db.String(50), primary_key=True)
    recipe_id = db.Column(db.Integer, primary_key=True)
    attrib_id = db.Column(db.Integer, primary_key=True)
    op_compare = db.Column(db.String(5), nullable=False, default=Operation.EQ)
    val_required = db.Column(db.Float, nullable=False, default=1.0)

    def to_dict(self):
        data = {
            "attrib_id": self.attrib_id,
            "op_compare": self.op_compare,
            "val_required": attrib_val_to_json(
                self.game_token, self.attrib_id, self.val_required)
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, recipe_id):
        """Processes nested list data before delegating to DictHydrator."""
        attrib_id = data.get('attrib_id')
        updates = {
            'val_required': attrib_val_from_json(
                game_token, attrib_id, data.pop('val_required', None))
        }
        return super().from_dict(
            data, game_token, recipe_id=recipe_id, **updates)

    def is_satisfied(self, val):
        """Checks if a specific value meets this requirement."""
        from app.src.logic_event import apply_operation
        return apply_operation(
            val, self.val_required, self.op_compare, attrib=self.attrib)

    @property
    def display(self):
        symbol = Operation.Repr.get(self.op_compare, self.op_compare)
        val_str = self.attrib.format_value(self.val_required)
        return f"{symbol} {val_str}"

    recipe = db.relationship(
        'Recipe',
        back_populates='attrib_reqs',
        foreign_keys=[game_token, recipe_id],
        overlaps="recipe,reqs")
    attrib = db.relationship(
        'Attrib',
        back_populates='reqs',
        foreign_keys=[game_token, attrib_id],
        overlaps="recipe,attrib_reqs")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'recipe_id'],
            ['recipes.game_token', 'recipes.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'attrib_id'],
            ['attribs.game_token', 'attribs.id'], ondelete='CASCADE'),
    )

# ------------------------------------------------------------------------
# State and Navigation
# ------------------------------------------------------------------------

class Progress(db.Model, DictHydrator):
    """
    Table for all timed activities.
    Produce Items in a Pile via Recipes.
    """
    __tablename__ = 'progress'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    
    # what we're making
    recipe_id = db.Column(db.Integer, nullable=False)
    product_id = db.Column(db.Integer, nullable=False) # recipe.product_id copy
    owner_id = db.Column(db.Integer, nullable=False)

    # who is involved -- they must still be present and have ingredients
    host_id = db.Column(db.Integer, nullable=False)
    char_id = db.Column(db.Integer)
    loc_id = db.Column(db.Integer)
    
    # status
    start_time = db.Column(db.DateTime)
    batches_processed = db.Column(db.Integer, default=0)
    stop_at = db.Column(db.Float)

    def to_dict(self):
        data = {
            "recipe_id": self.recipe_id,
            "owner_id": self.owner_id,
            "host_id": self.host_id,
            "char_id": self.char_id,
            "loc_id": self.loc_id,
            "start_time": timeToStr(self.start_time),
            "batches_processed": self.batches_processed,
            "stop_at": self.stop_at,
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token):
        obj = super().from_dict(data, game_token)

        from app.models import Recipe
        recipe = db.session.get(Recipe, (game_token, data.get('recipe_id')))
        if recipe:
            obj.product_id = recipe.product_id

        obj.start_time = timeFromStr(data.get('start_time'))

        return obj

    host = db.relationship(
        'Entity', 
        back_populates='progress_records',
        foreign_keys=[game_token, host_id],
        overlaps="recipe,progress_records")
    recipe = db.relationship(
        'Recipe',
        foreign_keys=[game_token, recipe_id],
        overlaps="host,progress_records")

    @property
    def product(self):
        return self.recipe.product if self.recipe else None

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'recipe_id'],
            ['recipes.game_token', 'recipes.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'product_id'],
            ['items.game_token', 'items.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'host_id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
         db.UniqueConstraint(
            'game_token', 'host_id', 'product_id', name='_host_product_uc'),
    )

# ------------------------------------------------------------------------
# Global Configuration
# ------------------------------------------------------------------------

class Scenario(db.Model, DictHydrator):
    """
    Stores scenario-wide metadata and win conditions for a loaded scenario.
    The model uses a game token to identify the loaded scenario instance,
    but does not create or manage game tokens itself.
    """
    __tablename__ = 'scenario'
    game_token = db.Column(db.String(50), primary_key=True)
    title = db.Column(db.String(255), nullable=False, default='New Scenario')
    description = db.Column(db.Text)

    # Metadata tags for scenario browsing
    tag_introduce_order = db.Column(db.Integer, default=50)
    tag_best_order = db.Column(db.Integer, default=50)
    tag_progress_type = db.Column(db.String(20), default='RPG')
    tag_complete = db.Column(db.String(50), default='02 Under Construction')

    def to_dict(self):
        data = {
            "title": self.title,
            "description": self.description,
            "win_reqs": [
                wr.to_dict() for wr in
                sorted(self.win_reqs, key=lambda x: x.order_index)
            ],
            "tag_introduce_order": self.tag_introduce_order,
            "tag_best_order": self.tag_best_order,
            "tag_progress_type": self.tag_progress_type,
            "tag_complete": self.tag_complete,
        }
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        scenario = super().from_dict(data, game_token)
        for order_index, wr_data in enumerate(data.get('win_reqs', [])):
            scenario.win_reqs.append(
                WinRequirement.from_dict(wr_data, game_token, order_index)
            )
        return scenario

    win_reqs = db.relationship(
        'WinRequirement',
        back_populates='scenario',
        foreign_keys="[WinRequirement.game_token]",
        cascade="all, delete-orphan",
        order_by="WinRequirement.order_index",
        overlaps="scenario,item,char,loc,attrib")

class WinRequirement(db.Model, DictHydrator):
    __tablename__ = 'win_requirements'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    order_index = db.Column(db.Integer, default=0)
    item_id = db.Column(db.Integer)
    quantity = db.Column(db.Float)
    char_id = db.Column(db.Integer)
    loc_id = db.Column(db.Integer)
    attrib_id = db.Column(db.Integer)
    attrib_value = db.Column(db.Float)

    def to_dict(self):
        data = {
            "item_id": self.item_id,
            "quantity": self.quantity,
            "char_id": self.char_id,
            "loc_id": self.loc_id,
            "attrib_id": self.attrib_id,
            "attrib_value": attrib_val_to_json(
                self.game_token, self.attrib_id, self.attrib_value)
        }
        return self.to_dict_sparse(data)

    @classmethod
    def from_dict(cls, data, game_token, order_index):
        updates = {}
        attrib_id = data.get('attrib_id')
        if attrib_id:
            attrib_val_raw = data.pop('attrib_value', None)
            updates['attrib_value'] = attrib_val_from_json(
                game_token, attrib_id, attrib_val_raw)
        return super().from_dict(
            data, game_token, order_index=order_index, **updates)

    scenario = db.relationship(
        'Scenario',
        back_populates='win_reqs',
        foreign_keys=[game_token],
        overlaps="item,char,loc,attrib")
    item = db.relationship(
        'Item',
        foreign_keys=[game_token, item_id],
        overlaps="scenario,char,loc,attrib")
    char = db.relationship(
        'Character',
        foreign_keys=[game_token, char_id],
        overlaps="scenario,item,loc,attrib")
    loc = db.relationship(
        'Location',
        foreign_keys=[game_token, loc_id],
        overlaps="scenario,item,char,attrib")
    attrib = db.relationship(
        'Attrib',
        foreign_keys=[game_token, attrib_id],
        overlaps="scenario,item,char,loc")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token'],
            ['scenario.game_token'], 
            ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'item_id'],
            ['items.game_token', 'items.id'], 
            ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'char_id'],
            ['characters.game_token', 'characters.id'], 
            ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'loc_id'],
            ['locations.game_token', 'locations.id'], 
            ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'attrib_id'],
            ['attribs.game_token', 'attribs.id'], 
            ondelete='CASCADE'),
    )

class IdSequence(db.Model):
    """
    Maintains the next available integer ID for a scenario.

    Unlike database sequences or UUIDs, IDs are kept small and human-readable
    because scenarios are imported from and exported to JSON intended for manual
    editing. When a scenario is loaded, this value is initialized from the
    highest ID found in the JSON. New objects created during editing obtain IDs
    from this sequence.
    """
    __tablename__ = "id_sequence"
    game_token = db.Column(db.String(50), primary_key=True)
    next_id = db.Column(db.Integer, default=HIGHEST_RESERVED_ID + 1)

    @classmethod
    def generate_next_id(cls, game_token):
        """
        Generate per-token IDs, useful across entities.
        Increments the counter and returns the new ID.
        """
        # Lock the record for this token until we call commit().
        stmt = (
            select(cls)
            .filter_by(game_token=game_token)
            .with_for_update()
        )
        row = db.session.execute(stmt).scalar_one_or_none()
        assigned_id = row.next_id
        row.next_id += 1
        
        return assigned_id

# ------------------------------------------------------------------------
# Session Tracking
# ------------------------------------------------------------------------

class UserInteraction(db.Model):
    __tablename__ = 'user_interactions'
    game_token = db.Column(db.String(50), primary_key=True)
    username = db.Column(db.String(50), primary_key=True)
    route = db.Column(db.String(50), primary_key=True)
    entity_id = db.Column(db.String(20), primary_key=True)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

class GameMessage(db.Model):
    __tablename__ = 'game_messages'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())
    message = db.Column(db.Text)
    count = db.Column(db.Integer, default=1)
