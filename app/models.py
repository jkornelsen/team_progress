from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import ARRAY, NUMRANGE
from .utils import parse_numrange

db = SQLAlchemy()

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
            "description": self.description
        }

    @classmethod
    def from_dict(cls, data, game_token):
        """Standard base hydration for all entities."""
        fields = {k: v for k, v in data.items()
            if hasattr(cls, k) and not isinstance(v, (list, dict))}
        return cls(game_token=game_token, **fields)

    @classmethod
    def get_or_new(cls, game_token, id):
        if id is None:
            # Create a fresh instance.
            # Does not generate id or write to db.
            return cls(game_token=game_token)
        return cls.query.filter_by(game_token=game_token, id=id).first_or_404()

    piles = db.relationship(
        'Pile', 
        back_populates='owner', 
        foreign_keys="[Pile.game_token, Pile.owner_id]",
        cascade="all, delete-orphan") # Deletes piles if Entity is deleted
    attrib_values = db.relationship(
        'AttribValue', 
        back_populates='subject',
        foreign_keys="[AttribValue.game_token, AttribValue.subject_id]",
        cascade="all, delete-orphan")
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
            item.attrib_values.append(AttribValue(
                game_token=game_token, attrib_id=attr_pair[0], value=attr_pair[1]
            ))
        return item

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
            "destinations": [d.to_dict() for d in self.exits],
        })
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        loc = super().from_dict(data, game_token)
        for i_data in data.get('items', []):
            loc.piles.append(Pile(
                game_token=game_token,
                owner=loc, 
                **i_data))
        for d_data in data.get('destinations', []):
            loc.exits.append(LocationDest(
                    game_token=game_token, 
                    loc1=loc, 
                    **d_data))
        return loc

    characters_here = db.relationship(
        'Character',
        back_populates='location',
        foreign_keys="[Character.game_token, Character.location_id]")
    travelling_here = db.relationship(
        'TravelProgress',
        back_populates='destination',
        foreign_keys="[TravelProgress.game_token, TravelProgress.dest_id]")
    exits = db.relationship(
        'LocationDest',
        back_populates='loc1',
        foreign_keys="[LocationDest.game_token, LocationDest.loc1_id]",
        cascade="all, delete-orphan",
        overlaps="entrances")
    entrances = db.relationship(
        'LocationDest',
        back_populates='loc2',
        foreign_keys="[LocationDest.game_token, LocationDest.loc2_id]",
        cascade="all, delete-orphan",
        overlaps="exits")
    item_refs = db.relationship(
        'ItemRef',
        back_populates='location',
        foreign_keys="[ItemRef.game_token, ItemRef.loc_id]",
        viewonly=True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
    )
    __mapper_args__ = {'polymorphic_identity': 'location'}

class Character(Entity):
    """The primary entity for role-playing scenarios.
    The location_id foreign key means that Location must be defined first.
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
    """Functional or informational stats for other entities."""
    __tablename__ = 'attribs'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    enum_list = db.Column(ARRAY(db.Text))
    is_binary = db.Column(db.Boolean, default=False)

    def to_dict(self):
        """Exports the attribute definition (type and states)."""
        data = super().to_dict()
        data.update({
            "is_binary": self.is_binary,
            "enum_list": self.enum_list or []
        })
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        return super().from_dict(data, game_token)

    attrib_values = db.relationship(
        'AttribValue',
        back_populates='attrib',
        foreign_keys="[AttribValue.game_token, AttribValue.attrib_id]",
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

class Event(Entity):
    """Actions and things that can happen, typically with chance.
    Many events use or change the Attrib values of other entities.
    """
    __tablename__ = 'events'
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True)
    toplevel = db.Column(db.Boolean, default=False)
    outcome_type = db.Column(db.String(20), nullable=False) # e.g. fourway, numeric
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
            "trigger_chance": self.trigger_chance,
            "numeric_range": self.numeric_range,
            "single_number": self.single_number,
            "selection_strings": self.selection_strings,
            "determinants": [
                {
                    "label": d.label, "attrib_id": d.attrib_id, 
                    "operation": d.operation, "mode": d.mode
                } for d in self.determinants
            ],
            "effects": [
                {"attrib_id": e.attrib_id, "multiplier": e.multiplier} 
                for e in self.effects
            ]
        })
        return data

    @classmethod
    def from_dict(cls, data, game_token):
        dets = data.pop('determinants', [])
        effects = data.pop('effects', [])
        event = super().from_dict(data, game_token)
        event.determinants = [
            EventDeterminant(game_token=game_token, **d) for d in dets
        ]
        event.effects = [
            EventEffect(game_token=game_token, **e) for e in effects
        ]
        return event

    determinants = db.relationship(
        'EventDeterminant',
        back_populates='event',
        cascade="all, delete-orphan")
    effects = db.relationship(
        'EventEffect',
        back_populates='event',
        cascade="all, delete-orphan")

    __table_args__ = (
        db.ForeignKeyConstraint(
            ['game_token', 'id'],
            ['entities.game_token', 'entities.id'], ondelete='CASCADE'),
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
    game_token = db.Column(db.String(50), primary_key=True)
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
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

class AttribValue(db.Model):
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

class LocationDest(db.Model):
    __tablename__ = 'loc_destinations'
    game_token = db.Column(db.String(50), primary_key=True)
    loc1_id = db.Column(db.Integer, primary_key=True)
    loc2_id = db.Column(db.Integer, primary_key=True)
    door1 = db.Column(ARRAY(db.Integer))
    door2 = db.Column(ARRAY(db.Integer))
    duration = db.Column(db.Integer, nullable=False)
    bidirectional = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            "loc2_id": self.loc2_id,
            "duration": self.duration,
            "door1": self.door1,
            "door2": self.door2,
            "bidirectional": self.bidirectional
        }

    loc1 = db.relationship(
        'Location',
        back_populates='exits',
        foreign_keys=[game_token, loc1_id],
        overlaps="loc2,entrances")
    loc2 = db.relationship(
        'Location',
        back_populates='entrances',
        foreign_keys=[game_token, loc2_id],
        overlaps="loc1,exits")

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

class SourceRole:
    ACTOR = 'actor'           # The subject performing the event
    TARGET = 'target'         # Another character nearby
    ACTOR_ITEM = 'actor_item' # An item held by the actor
    TARGET_ITEM = 'target_item' # An item held by the target
    LOCATION = 'location'     # The room itself
    GLOBAL = 'global'         # General Storage (ID 1)

    ALL = [ACTOR, TARGET, ACTOR_ITEM, TARGET_ITEM, LOCATION, GLOBAL]

class EventDeterminant(db.Model):
    __tablename__ = 'event_determinants'
    id = db.Column(db.Integer, primary_key=True)
    game_token = db.Column(db.String(50), primary_key=True)
    event_id = db.Column(db.Integer, nullable=False)
    
    label = db.Column(db.String(50)) # e.g., "Accuracy"
    source_role = db.Column(db.String(20), default=SourceRole.ACTOR)
    attrib_id = db.Column(db.Integer)
    item_id = db.Column(db.Integer) # If null, use attrib_id. If both, sum? Usually one.
    
    operation = db.Column(db.String(1), default='+') # +, -, *, /
    mode = db.Column(db.String(10), default='')     # '', 'log', 'half'

    event = db.relationship('Event', back_populates='determinants', foreign_keys=[game_token, event_id])

    __table_args__ = (
        db.ForeignKeyConstraint(['game_token', 'event_id'], ['events.game_token', 'events.id'], ondelete='CASCADE'),
    )

class EventEffect(db.Model):
    """What changes when the event is applied."""
    __tablename__ = 'event_effects'
    id = db.Column(db.Integer, primary_key=True)
    game_token = db.Column(db.String(50), primary_key=True)
    event_id = db.Column(db.Integer, nullable=False)
    
    target_role = db.Column(db.String(20), default=SourceRole.TARGET)
    attrib_id = db.Column(db.Integer)
    item_id = db.Column(db.Integer)
    multiplier = db.Column(db.Float, default=1.0) # Outcome * Multiplier

    event = db.relationship('Event', back_populates='effects', foreign_keys=[game_token, event_id])

    __table_args__ = (
        db.ForeignKeyConstraint(['game_token', 'event_id'], ['events.game_token', 'events.id'], ondelete='CASCADE'),
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
        # Pop nested lists so the constructor doesn't crash
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
    game_token = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())
    message = db.Column(db.Text)
    count = db.Column(db.Integer, default=1)
