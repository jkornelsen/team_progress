"""
Relation tables must be created after base tables because
the keys depend on their prior existence.
"""
from .db_serializable import coldef

tables_to_create = {
    # Item
    'recipe_sources': f"""
        {coldef('game_token')},
        recipe_id integer not null,
        item_id integer not null,
        q_required real not null,
        preserve boolean not null,
        PRIMARY KEY (game_token, recipe_id, item_id),
        FOREIGN KEY (game_token, recipe_id)
            REFERENCES recipes (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    'recipe_byproducts': f"""
        {coldef('game_token')},
        recipe_id integer not null,
        item_id integer not null,
        rate_amount real not null,
        PRIMARY KEY (game_token, recipe_id, item_id),
        FOREIGN KEY (game_token, recipe_id)
            REFERENCES recipes (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    'recipe_attrib_reqs': f"""
        {coldef('game_token')},
        recipe_id integer not null,
        attrib_id integer not null,
        value_range numrange not null,
        show_max boolean,
        PRIMARY KEY (game_token, recipe_id, attrib_id),
        FOREIGN KEY (game_token, recipe_id)
            REFERENCES recipes (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    # Character
    'char_items': f"""
        {coldef('game_token')},
        char_id integer not null,
        item_id integer not null,
        quantity real not null,
        slot varchar(50),
        PRIMARY KEY (game_token, char_id, item_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    # Location
    'loc_destinations': f"""
        {coldef('game_token')},
        loc1_id integer not null,
        loc2_id integer not null,
        duration integer not null,
        door1 integer[2],
        door2 integer[2],
        bidirectional boolean not null,
        PRIMARY KEY (game_token, loc1_id, loc2_id),
        FOREIGN KEY (game_token, loc1_id)
            REFERENCES locations (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, loc2_id)
            REFERENCES locations (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    'loc_items': f"""
        {coldef('game_token')},
        loc_id integer not null,
        item_id integer not null,
        is_ref boolean not null,
        quantity real not null,
        position integer[2],
        PRIMARY KEY (game_token, loc_id, item_id, position),
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    # Event
    'event_determining': f"""
        -- values that are used to determine the event outcome
        {coldef('game_token')},
        event_id integer not null,
        attrib_id integer,
        item_id integer,
        operation varchar(1) not null
            CHECK (operation IN ('+', '-', '*', '/')),
        mode varchar(4) not null
            CHECK (mode IN ('', 'log', 'half')),
        label varchar(50),  -- for example "Evasion" from Dexterity
        FOREIGN KEY (game_token, event_id)
            REFERENCES events (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    'event_changed': f"""
        -- values that can be changed by event outcome
        {coldef('game_token')},
        event_id integer not null,
        attrib_id integer,
        item_id integer,
        UNIQUE (game_token, event_id, attrib_id, item_id),
        FOREIGN KEY (game_token, event_id)
            REFERENCES events (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    'event_triggers': f"""
        -- entities that link to events or call them by trigger
        {coldef('game_token')},
        event_id integer not null,
        char_id integer,
        item_id integer,
        loc_id integer,
        UNIQUE (game_token, event_id, char_id, item_id, loc_id),
        FOREIGN KEY (game_token, event_id)
            REFERENCES events (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    # Attrib
    'attribs_of': f"""
        {coldef('game_token')},
        attrib_id integer not null,
        char_id integer,
        item_id integer,
        loc_id integer,
        value real not null,
        UNIQUE (game_token, attrib_id, char_id, item_id, loc_id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    # Overall
    'win_requirements': f"""
        {coldef('id')},
        item_id integer,
        quantity real,
        char_id integer,
        loc_id integer,
        attrib_id integer,
        attrib_value real,
        UNIQUE (game_token, item_id, char_id, loc_id, attrib_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE cascade
            DEFERRABLE initially deferred
        """,
    }
