from sqlalchemy import event
from sqlalchemy.dialects.postgresql import ARRAY, NUMRANGE
from .database import db
from .utils import parse_numrange

# Reserved ID in Entity table for universal storage.
# Can have up to one general pile for each Item.
# Progress can also be hosted generally rather than by char or at loc.
GENERAL_ID = 1

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

@event.listens_for(db.Model, 'init', propagate=True)
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

# ------------------------------------------------------------------------
# Entity Parent Class
# ------------------------------------------------------------------------

class Entity(db.Model):
    """Base table for all primary game objects."""
    __tablename__ = 'entities'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    def to_dict(self):
        """Base export for shared entity fields."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "attribs": [
                [av.attrib_id, av.value] 
                for av in self.attrib_values
            ],
            "abilities": [link.event_id for link in self._ability_links]
        }

    @classmethod
    def from_dict(cls, data, game_token):
        """Standard base hydration for all entities."""
        # Skip lists and dicts because they may be nested relationships.
        # Subclass from_dict() methods will need to handle them instead.
        fields = {k: v for k, v in data.items()
            if hasattr(cls, k) and not isinstance(v, (list, dict))}
        obj = cls(game_token=game_token, **fields)

        for a_data in data.get('attribs', []):
            if isinstance(a_data, list) and len(a_data) == 2:
                obj.attrib_values.append(AttribVal(
                    game_token=game_token,
                    attrib_id=a_data[0],
                    value=a_data[1]
                ))
        for event_id in data.get('abilities', []):
            obj._ability_links.append(EntityAbility(
                game_token=game_token,
                event_id=event_id
            ))
        return obj

    def get_deep_relationships(self):
        d = deep_rel('attrib_values', AttribVal, 'subject_id')
        d.update(deep_rel('_ability_links', EntityAbility, 'entity_id'))
        return d

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
        'polymorphic_identity': 'entity'
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
    __tablename__ = 'items'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    storage_type = db.Column(
        db.String(1), nullable=False, default=StorageType.CARRIED)
    q_limit = db.Column(db.Float, default=0.0)
    toplevel = db.Column(db.Boolean, default=False)
    masked = db.Column(db.Boolean, default=False)
    counted_for_unmasking = db.Column(db.Boolean, default=False)

    def to_dict(self):
        data = super().to_dict()
        data.update({
            "storage_type": self.storage_type,
            "q_limit": self.q_limit,
            "toplevel": self.toplevel,
            "masked": self.masked,
            "attribs": [[v.attrib_id, v.value] for v in self.attrib_values],
            "recipes": [r.to_dict() for r in self.recipes]
        })
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        item = super().from_dict(data, game_token)
        for r_data in data.get('recipes', []):
            item.recipes.append(Recipe.from_dict(r_data, game_token))
        for attr_pair in data.get('attribs', []):
            item.attrib_values.append(AttribVal(
                game_token=game_token, attrib_id=attr_pair[0], value=attr_pair[1]
            ))
        return item

    def get_deep_relationships(self):
        d = super().get_deep_relationships()
        d.update(deep_rel('recipes', Recipe, 'product_id'))
        return d

    in_piles = db.relationship(
        'Pile',
        back_populates='item',
        foreign_keys="[Pile.game_token, Pile.item_id]",
        viewonly=True)
    recipes = db.relationship(
        'Recipe', 
        back_populates='product', 
        foreign_keys="[Recipe.game_token, Recipe.product_id]",
        cascade="all, delete-orphan")
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

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
        db.CheckConstraint(
            f"storage_type IN {StorageType.ALL_CODES}", 
            name="check_storage_type_valid"),
    )
    __mapper_args__ = {'polymorphic_identity': 'item'}

class Location(Entity):
    """Allows items or characters to be in different places,
    making the scenario bigger.

    SINGLETON: Each is a unique container for Piles and ItemRefs.
    """
    __tablename__ = 'locations'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    toplevel = db.Column(db.Boolean, default=False)
    masked = db.Column(db.Boolean, default=False)
    dimensions = db.Column(ARRAY(db.Integer), default=None)
    excluded = db.Column(ARRAY(db.Integer), default=None)

    def to_dict(self):
        data = super().to_dict()
        data.update({
            "dimensions": self.dimensions,
            "excluded": self.excluded,
            "toplevel": self.toplevel,
            "masked": self.masked,
            "items": [
                {"item_id": p.item_id, "quantity": p.quantity, "position": p.position} 
                for p in self.piles
            ],
            "item_refs": [ir.to_dict() for ir in self.item_refs],
            "destinations": [d.to_dict() for d in self.routes_forward],
        })
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        loc = super().from_dict(data, game_token)
        # Handle list fields skipped by the base method
        loc.dimensions = data.get('dimensions')
        loc.excluded = data.get('excluded')
        # Relationships
        for i_data in data.get('items', []):
            loc.piles.append(Pile(
                game_token=game_token,
                owner=loc, 
                **i_data))
        for ir_data in data.get('item_refs', []):
            loc.item_refs.append(ItemRef(
                game_token=game_token,
                loc_id=loc.id,
                **ir_data))
        for d_data in data.get('destinations', []):
            loc.routes_forward.append(LocDest(
                    game_token=game_token, 
                    loc1=loc, 
                    **d_data))
        return loc

    def get_deep_relationships(self):
        d = super().get_deep_relationships()
        d.update(deep_rel('item_refs', ItemRef, 'loc_id'))
        d.update(deep_rel('routes_forward', LocDest, 'loc1_id'))
        return d

    @property
    def exits(self):
        """
        Any route where we are loc1 (always an exit)
        Any route where we are loc2 AND it is bidirectional
        """
        forward = [r for r in self.routes_forward]
        backward = [r for r in self.routes_backward if r.bidirectional]
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
    travelling_here = db.relationship(
        'TravelProgress',
        back_populates='destination',
        foreign_keys="[TravelProgress.game_token, TravelProgress.dest_id]")
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

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
    )
    __mapper_args__ = {'polymorphic_identity': 'location'}

class Character(Entity):
    """The primary entity for role-playing scenarios.
    The location_id foreign key means that Location must be defined first.

    SINGLETON: Each is a unique actor and container for Piles.
    """
    __tablename__ = 'characters'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    toplevel = db.Column(db.Boolean, default=False)
    travel_group = db.Column(db.String(100))
    position = db.Column(ARRAY(db.Integer), default=None)
    location_id = db.Column(db.Integer)

    def to_dict(self):
        data = super().to_dict()
        data.update({
            "toplevel": self.toplevel,
            "location_id": self.location_id,
            "position": self.position,
            "travel_group": self.travel_group,
            "items": [
                {"item_id": p.item_id, "quantity": p.quantity, "slot": p.slot} 
                for p in self.piles
            ]
        })
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        char = super().from_dict(data, game_token)
        for i_data in data.get('items', []):
            char.piles.append(Pile(game_token=game_token, **i_data))
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
    __mapper_args__ = { 'polymorphic_identity': 'character' }

class Attrib(Entity):
    """
    Functional or informational stats for other entities.
    Stats (Str), states (Is Open), or properties (Color).
    Can be numeric, boolean, or enumerated list.

    BLUEPRINT: Defines a property (e.g., 'Strength').
    Actual values for entities are stored in 'AttribVal' instances.
    """
    __tablename__ = 'attribs'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    enum_list = db.Column(ARRAY(db.Text))
    is_binary = db.Column(db.Boolean, default=False)

    def to_dict(self):
        """Exports the attribute definition (type and states)."""
        data = super().to_dict()
        data.pop("attribs", None) 
        data.update({
            "is_binary": self.is_binary,
            "enum_list": self.enum_list or []
        })
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        data_copy = data.copy()
        data_copy.pop("attribs", None)
        return super().from_dict(data_copy, game_token)

    attrib_values = db.relationship(
        'AttribVal',
        back_populates='attrib',
        foreign_keys="[AttribVal.game_token, AttribVal.attrib_id]",
        viewonly=True)
    requirements = db.relationship(
        'RecipeAttribReq',
        back_populates='attrib',
        foreign_keys="[RecipeAttribReq.game_token, RecipeAttribReq.attrib_id]",
        viewonly=True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['id', 'game_token'],
            ['entities.id', 'entities.game_token'], ondelete='CASCADE'),
    )
    __mapper_args__ = { 'polymorphic_identity': 'attrib' }


class OutcomeType:
    FOURWAY ='fourway'        # strong/weak success or failure
    NUMERIC = 'numeric'       # such as a damage number
    DETERMINED = 'determined' # calculate from a single base number
    SELECT = 'selection'      # random selection from a list
    COORDS = 'coordinates'    # random coordinates from a location's grid
    ROLLER = 'dice_system'    # manual inputs for standardized system

    ALL = [FOURWAY, NUMERIC, DETERMINED, SELECT, COORDS, ROLLER]

class RollerType:
    """System for ROLLER outcome type"""
    DND = 'dnd'
    IRONSWORN = 'ironsworn'

    ALL = [DND, IRONSWORN]

class Event(Entity):
    """Actions and things that can happen, typically with chance.
    Many events use or change the Attrib values of other entities.
    """
    __tablename__ = 'events'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    toplevel = db.Column(db.Boolean, default=False)
    outcome_type = db.Column(
        db.String(20), nullable=False, default=OutcomeType.FOURWAY)
    roller_type = db.Column(
        db.String(20), nullable=False, default=RollerType.DND)
    trigger_chance = db.Column(db.Float, default=0.0)
    numeric_range = db.Column(ARRAY(db.Integer)) # [min, max]
    single_number = db.Column(db.Float, default=0.0)
    selection_strings = db.Column(db.Text)

    def to_dict(self):
        """Exports the event logic including dice ranges and modifiers."""
        data = super().to_dict()
        data.update({
            "toplevel": self.toplevel,
            "outcome_type": self.outcome_type,
            "roller_type": self.roller_type,
            "trigger_chance": self.trigger_chance,
            "numeric_range": self.numeric_range,
            "single_number": self.single_number,
            "selection_strings": self.selection_strings,
            "determinants": [d.to_dict() for d in self.determinants],
            "effects": [e.to_dict() for e in self.effects],
        })
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        event = super().from_dict(data, game_token)
        # Handle list fields skipped by the base method
        event.numeric_range = data.get('numeric_range') 
        # Relationships
        dets = data.get('determinants', [])
        effects = data.get('effects', [])
        event.factors = [
            EventFactor(game_token=game_token, event_id=event.id,
                        usage_type=Participant.IN, **d) 
            for d in dets
        ] + [
            EventFactor(game_token=game_token, event_id=event.id,
                        usage_type=Participant.OUT, **e) 
            for e in effects
        ]
        return event

    def get_deep_relationships(self):
        d = super().get_deep_relationships()
        d.update(deep_rel('factors', EventFactor, 'event_id'))
        return d

    @property
    def determinants(self):
        return [f for f in self.factors if f.usage_type == FactorUsage.IN]

    @property
    def effects(self):
        return [f for f in self.factors if f.usage_type == FactorUsage.OUT]

    factors = db.relationship(
        'EventFactor',
        back_populates='event',
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
    __mapper_args__ = {'polymorphic_identity': 'event'}

ENTITIES = {
    'items': Item,
    'locations': Location,
    'characters': Character,
    'attribs': Attrib,
    'events': Event
}

# ------------------------------------------------------------------------
# Association Tables (Many-to-Many)
# ------------------------------------------------------------------------

class Pile(db.Model):
    """Consolidated pile of items either:
    * held by a Character
    * or at a Location
    * or neither; the general pile of that Item
    The 'owner' relationship handles all of these cases.
    """
    __tablename__ = 'piles'
    # Single Primary Key: Postgres handles autoincrement globally without drama
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # Still important for foreign keys
    game_token = db.Column(db.String(50), index=True, nullable=False)
    owner_id = db.Column(db.Integer)
    item_id = db.Column(db.Integer)
    # not relevant for universal storage
    position = db.Column(ARRAY(db.Integer), default=None)
    quantity = db.Column(db.Float, nullable=False, default=0.0)
    # optional for carried items (equipment)
    slot = db.Column(db.String(50))

    def to_dict(self):
        """Exports pile state, excluding internal database IDs."""
        return {
            "owner_id": self.owner_id,
            "item_id": self.item_id,
            "quantity": self.quantity,
            "position": self.position,  # Will be a tuple/list or None
            "slot": self.slot
        }

    @classmethod
    def from_dict(cls, data, game_token):
        """Creates a Pile instance from a dictionary without needing an ID."""
        pos = data.get('position')
        if isinstance(pos, list):
            pos = tuple(pos)
        return cls(
            game_token=game_token,
            owner_id=data.get('owner_id'),
            item_id=data.get('item_id'),
            quantity=data.get('quantity', 0.0),
            position=pos,
            slot=data.get('slot')
        )

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
    )

class AttribVal(db.Model):
    """Values of an Attrib applied to an Entity (Item, Character, or Location)."""
    __tablename__ = 'attrib_values'
    game_token = db.Column(db.String(50), primary_key=True)
    attrib_id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.Float, nullable=False, default=0.0)

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

class LocDest(db.Model):
    __tablename__ = 'loc_destinations'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    game_token = db.Column(db.String(50), index=True, nullable=False)
    loc1_id = db.Column(db.Integer, nullable=False)
    loc2_id = db.Column(db.Integer, nullable=False)
    door1 = db.Column(ARRAY(db.Integer))
    door2 = db.Column(ARRAY(db.Integer))
    duration = db.Column(db.Integer, nullable=False, default=1)
    bidirectional = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            "loc2_id": self.loc2_id,
            "duration": self.duration,
            "door1": self.door1,
            "door2": self.door2,
            "bidirectional": self.bidirectional
        }

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

    def to_dict(self):
        return {
            "item_id": self.item_id
        }

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
    """References a field to grab for an event factor."""

    # --- Role of the Anchor Entity ---
    SUBJECT = 'subj' # Context-driven e.g. player char
    OTHER1 = '2nd'   # Typically user-selected e.g. enemy
    OTHER2 = '3rd'   # Environment or third participant
    UNIV = 'univ'    # General storage item, can be multiple

    # --- Depth Traversal ---
    # False: Use the anchor entity itself.
    # True: Select an item pile inside the anchor.
    # Only valid if the anchor resolves to a Character or Location.
    ChildItem = False

    # --- Field ---
    ATTR = 'attr'  # Fetch AttribVal
    QTY  = 'qty'   # Fetch pile quantity

    # --- Usage ---
    IN = 'in'   # Determinant
    OUT = 'out' # Effect

    # --- Constraints ---
    ALL_ROLES = [SUBJECT, OTHER1, OTHER2, UNIV]
    ALL_FIELDS = [ATTR, QTY]
    ALL_USAGE = [IN, OUT]

class Operation:
    ADD = '+'
    SUB = '-'
    MULT = '*'
    DIV = '/'
    VAL_TO_POW = 'x^'
    POW_OF_VAL = '^x'

    Repr = {
        ADD:        {'symbol': '+',  'text': 'Add'},
        SUB:        {'symbol': '−',  'text': 'Subtract'},
        MULT:       {'symbol': '×',  'text': 'Multiply'},
        DIV:        {'symbol': '÷',  'text': 'Divide'},
        VAL_TO_POW: {'symbol': 'xⁿ', 'text': 'Val To Power'},
        POW_OF_VAL: {'symbol': 'nˣ', 'text': 'Power Of Val'},
    }

    ALL = [ADD, SUB, MULT, DIV, VAL_TO_POW, POW_OF_VAL]

class EventFactor(db.Model):
    """
    Defines which attrib values or item quantities are used to determine
        an Event's outcome, and how they apply.
    Can be a Determinant (input) or an Effect (output).

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
    __tablename__ = 'event_factors'
    
    id = db.Column(db.Integer, primary_key=True)
    game_token = db.Column(db.String(50), primary_key=True)
    event_id = db.Column(db.Integer, nullable=False)

    # --- Identity & Logic Type ---
    usage_type = db.Column(db.String(3), nullable=False, default=Participant.IN)
    label = db.Column(db.String(50)) # "Base Damage", "Armor Soak", etc.

    # --- Retrieval Logic (The "Who & What") ---
    role = db.Column(db.String(10), nullable=False, default=Participant.SUBJECT)
    field = db.Column(db.String(10), nullable=False, default=Participant.ATTR)
    child_of_anchor = db.Column(db.Boolean, nullable=False, default=False)
    item_id = db.Column(db.Integer, nullable=True) # play dropdown if null
    attrib_id = db.Column(db.Integer, nullable=True)

    # --- Mathematical Transformation ---
    operation = db.Column(db.String(2), default=Operation.ADD) 
    modifier = db.Column(db.Float, default=1.0) # e.g. 2 ^ field val
    scaling = db.Column(db.String(10)) # curve: 'log', 'half'

    # Relationships
    event = db.relationship('Event', back_populates='factors', foreign_keys=[game_token, event_id])

    def to_dict(self):
        return {
            "id": self.id,
            "usage_type": self.usage_type,
            "label": self.label,
            "role": self.role,
            "field": self.field,
            "child_of_anchor": self.child_of_anchor,
            "item_id": self.item_id,
            "attrib_id": self.attrib_id,
            "operation": self.operation,
            "modifier": self.modifier,
            "scaling": self.scaling
        }

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'event_id'],
            ['events.game_token', 'events.id'], ondelete='CASCADE'),
        db.CheckConstraint(
            usage_type.in_(Participant.ALL_USAGE), name='check_usage_type_valid'),
        db.CheckConstraint(
            role.in_(Participant.ALL_ROLES), name='check_role_valid'),
        db.CheckConstraint(
            field.in_(Participant.ALL_FIELDS), name='check_field_valid'),
        db.CheckConstraint(
            operation.in_(Operation.ALL), name='check_operation_valid'),
    )

class EntityAbility(db.Model):
    """Who can call events, for example, a specific character. Shows a link
    to the event that sets the caller as the subject.
    """
    __tablename__ = 'entity_abilities'
    game_token = db.Column(db.String(50), primary_key=True)
    entity_id = db.Column(db.Integer, primary_key=True) # The Caller (Char/Loc/Item)
    event_id = db.Column(db.Integer, primary_key=True)  # The Event being called

    def to_dict(self):
        return {
            "event_id": self.event_id
        }

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

class Recipe(db.Model):
    __tablename__ = 'recipes'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, nullable=False)
    rate_amount = db.Column(db.Float, default=1.0)
    rate_duration = db.Column(db.Float, default=3.0)
    instant = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "product_id": self.product_id,
            "rate_amount": self.rate_amount,
            "rate_duration": self.rate_duration,
            "instant": self.instant,
            "sources": [
                {"item_id": s.item_id, "q_required": s.q_required, "preserve": s.preserve} 
                for s in self.sources
            ],
            "byproducts": [
                {"item_id": b.item_id, "rate_amount": b.rate_amount} 
                for b in self.byproducts
            ],
            "attrib_reqs": [
                {
                    "attrib_id": ar.attrib_id, 
                    "value_range": [ar.value_range.lower, ar.value_range.upper],
                    "show_max": ar.show_max
                } for ar in self.attrib_reqs
            ]
        }

    @classmethod
    def from_dict(cls, data, game_token):
        # Pop nested lists so the constructor doesn't try to handle them
        sources = data.pop('sources', [])
        byproducts = data.pop('byproducts', [])
        reqs = data.pop('attrib_reqs', [])
        
        recipe = cls(game_token=game_token, **data)
        
        # Re-insert nested data into relationships
        recipe.sources = [RecipeSource(game_token=game_token, **s) for s in sources]
        recipe.byproducts = [RecipeByproduct(game_token=game_token, **b) for b in byproducts]
        for ar in reqs:
            v_range = ar.get('value_range', [None, None])
            recipe.attrib_reqs.append(RecipeAttribReq(
                game_token=game_token, attrib_id=ar['attrib_id'],
                value_range=parse_numrange(v_range[0], v_range[1])
            ))
        return recipe

    def get_deep_relationships(self):
        d = deep_rel('sources', RecipeSource, 'recipe_id')
        d.update(deep_rel('byproducts', RecipeByproduct, 'recipe_id'))
        d.update(deep_rel('attrib_reqs', RecipeAttribReq, 'recipe_id'))
        return d

    @property
    def source_items(self):
        return [src.ingredient for src in self.sources]

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

class RecipeSource(db.Model):
    __tablename__ = 'recipe_sources'
    game_token = db.Column(db.String(50), primary_key=True)
    recipe_id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, primary_key=True) # Ingredient
    q_required = db.Column(db.Float, nullable=False)
    preserve = db.Column(db.Boolean, default=False)

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

class RecipeByproduct(db.Model):
    __tablename__ = 'recipe_byproducts'
    game_token = db.Column(db.String(50), primary_key=True)
    recipe_id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, primary_key=True)
    rate_amount = db.Column(db.Float, nullable=False)

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

class RecipeAttribReq(db.Model):
    __tablename__ = 'recipe_attrib_reqs'
    game_token = db.Column(db.String(50), primary_key=True)
    recipe_id = db.Column(db.Integer, primary_key=True)
    attrib_id = db.Column(db.Integer, primary_key=True)
    value_range = db.Column(NUMRANGE, nullable=False)
    show_max = db.Column(db.Boolean)

    recipe = db.relationship(
        'Recipe',
        back_populates='attrib_reqs',
        foreign_keys=[game_token, recipe_id],
        overlaps="recipe,requirements")
    attrib = db.relationship(
        'Attrib',
        back_populates='requirements',
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

class Progress(db.Model):
    """
    Base table for all timed activities, namely, Production and Travel.
    """
    __tablename__ = 'progress'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    activity_type = db.Column(db.String(20), nullable=False)
    
    # The Entity performing or hosting the activity
    host_id = db.Column(db.Integer, nullable=False)
    
    start_time = db.Column(db.DateTime)
    stop_time = db.Column(db.DateTime)
    batches_processed = db.Column(db.Integer, default=0)
    is_ongoing = db.Column(db.Boolean, default=False)

    host = db.relationship(
        'Entity', 
        back_populates='progress_records',
        foreign_keys=[game_token, host_id])

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'host_id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
    )

    __mapper_args__ = {
        'polymorphic_on': activity_type,
        'polymorphic_identity': 'base'
    }

class ProductionProgress(Progress):
    """Produce Items in a Pile via Recipes."""
    __tablename__ = 'production_progress'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, nullable=False)

    @property
    def product(self):
        return self.recipe.product if self.recipe else None

    recipe = db.relationship(
        'Recipe',
        foreign_keys=[game_token, recipe_id])

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['progress.game_token', 'progress.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'recipe_id'],
            ['recipes.game_token', 'recipes.id']),
    )
    __mapper_args__ = {'polymorphic_identity': 'production'}

class TravelProgress(Progress):
    """Characters moving between locations."""
    __tablename__ = 'travel_progress'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    dest_id = db.Column(db.Integer, nullable=False)

    destination = db.relationship(
        'Location',
        back_populates='travelling_here',
        foreign_keys=[game_token, dest_id])

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['progress.game_token', 'progress.id'], ondelete='CASCADE'),
        db.ForeignKeyConstraint(
            ['game_token', 'dest_id'],
            ['locations.game_token', 'locations.id']),
    )
    __mapper_args__ = {'polymorphic_identity': 'travel'}

# ------------------------------------------------------------------------
# Global Configuration
# ------------------------------------------------------------------------

class Overall(db.Model):
    __tablename__ = 'overall'
    game_token = db.Column(db.String(50), primary_key=True)
    title = db.Column(db.String(255), nullable=False, default='New Scenario')
    description = db.Column(db.Text)
    number_format = db.Column(db.String(5), default='en_US')
    slots = db.Column(ARRAY(db.Text))
    progress_type = db.Column(db.String(20))
    multiplayer = db.Column(db.Boolean, default=False)

    # Used to generate unique IDs per game token
    next_entity_id = db.Column(db.Integer, default=2)

    @classmethod
    def from_dict(cls, data, game_token):
        """Hydrates the global scenario settings and win conditions."""
        reqs = data.pop('win_reqs', [])
        overall = cls(game_token=game_token, **data)
        for r in reqs:
            overall.win_reqs.append(
                WinRequirement(game_token=game_token, **r)
            )
        return overall

    def to_dict(self):
        return {
            "title": self.title,
            "description": self.description,
            "number_format": self.number_format,
            "slots": self.slots or [],
            "progress_type": self.progress_type,
            "multiplayer": self.multiplayer
        }

    @classmethod
    def generate_next_id(cls, game_token):
        """
        Generate per-token IDs, useful across entities.
        Increments the counter and returns the new ID.
        """
        # Lock the record for this token until we call commit().
        overall = cls.query.filter_by(game_token=game_token).with_for_update().first()
        assigned_id = overall.next_entity_id
        overall.next_entity_id += 1
        
        return assigned_id

    win_reqs = db.relationship(
        'WinRequirement',
        back_populates='overall',
        foreign_keys="[WinRequirement.game_token]",
        cascade="all, delete-orphan",
        overlaps="overall,item,char,loc,attrib")

class WinRequirement(db.Model):
    __tablename__ = 'win_requirements'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer)
    quantity = db.Column(db.Float)
    char_id = db.Column(db.Integer)
    loc_id = db.Column(db.Integer)
    attrib_id = db.Column(db.Integer)
    attrib_value = db.Column(db.Float)

    overall = db.relationship(
        'Overall',
        back_populates='win_reqs',
        foreign_keys=[game_token],
        overlaps="item,char,loc,attrib")
    item = db.relationship(
        'Item',
        foreign_keys=[game_token, item_id],
        overlaps="overall,char,loc,attrib")
    char = db.relationship(
        'Character',
        foreign_keys=[game_token, char_id],
        overlaps="overall,item,loc,attrib")
    loc = db.relationship(
        'Location',
        foreign_keys=[game_token, loc_id],
        overlaps="overall,item,char,attrib")
    attrib = db.relationship(
        'Attrib',
        foreign_keys=[game_token, attrib_id],
        overlaps="overall,item,char,loc")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token'],
            ['overall.game_token']),
        db.ForeignKeyConstraint(
            ['game_token', 'item_id'],
            ['items.game_token', 'items.id']),
        db.ForeignKeyConstraint(
            ['game_token', 'char_id'],
            ['characters.game_token', 'characters.id']),
        db.ForeignKeyConstraint(
            ['game_token', 'loc_id'],
            ['locations.game_token', 'locations.id']),
        db.ForeignKeyConstraint(
            ['game_token', 'attrib_id'],
            ['attribs.game_token', 'attribs.id']),
    )

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
