"""
Relation tables must be created after base tables because
the keys depend on their prior existence.
"""
from .db_serializable import coldef

tables_to_create = {
    # Item
    'item_attribs': f"""
        {coldef('game_token')},
        item_id integer NOT NULL,
        attrib_id integer NOT NULL,
        value float(4) NOT NULL,
        PRIMARY KEY (game_token, item_id, attrib_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    'recipe_sources': f"""
        {coldef('game_token')},
        recipe_id integer NOT NULL,
        item_id integer NOT NULL,
        q_required float(4) NOT NULL,
        preserve boolean NOT NULL,
        PRIMARY KEY (game_token, recipe_id, item_id),
        FOREIGN KEY (game_token, recipe_id)
            REFERENCES recipes (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    'recipe_byproducts': f"""
        {coldef('game_token')},
        recipe_id integer NOT NULL,
        item_id integer NOT NULL,
        rate_amount float(4) NOT NULL,
        PRIMARY KEY (game_token, recipe_id, item_id),
        FOREIGN KEY (game_token, recipe_id)
            REFERENCES recipes (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    'recipe_attribs': f"""
        {coldef('game_token')},
        recipe_id integer NOT NULL,
        attrib_id integer NOT NULL,
        value float(4) NOT NULL,
        PRIMARY KEY (game_token, recipe_id, attrib_id),
        FOREIGN KEY (game_token, recipe_id)
            REFERENCES recipes (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    # Character
    'char_attribs': f"""
        {coldef('game_token')},
        char_id integer NOT NULL,
        attrib_id integer NOT NULL,
        value float(4) NOT NULL,
        PRIMARY KEY (game_token, char_id, attrib_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    'char_items': f"""
        {coldef('game_token')},
        char_id integer NOT NULL,
        item_id integer NOT NULL,
        quantity float(4) NOT NULL,
        slot varchar(50),
        PRIMARY KEY (game_token, char_id, item_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    # Location
    'loc_destinations': f"""
        {coldef('game_token')},
        loc_id integer NOT NULL,
        dest_id integer NOT NULL,
        distance float(4) NOT NULL,
        exit integer[2],
        entrance integer[2],
        PRIMARY KEY (game_token, loc_id, dest_id),
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, dest_id)
            REFERENCES locations (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    'loc_items': f"""
        {coldef('game_token')},
        loc_id integer NOT NULL,
        item_id integer NOT NULL,
        quantity float(4) NOT NULL,
        position integer[2],
        PRIMARY KEY (game_token, loc_id, item_id),
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    # Event
    'event_attribs': f"""
        {coldef('game_token')},
        event_id integer NOT NULL,
        attrib_id integer NOT NULL,
        determining boolean NOT NULL,
        PRIMARY KEY (game_token, event_id, attrib_id),
        FOREIGN KEY (game_token, event_id)
            REFERENCES events (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    'event_triggers': f"""
        {coldef('game_token')},
        event_id integer NOT NULL,
        item_id integer,
        loc_id integer,
        UNIQUE (game_token, event_id, item_id, loc_id),
        FOREIGN KEY (game_token, event_id)
            REFERENCES events (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    # Overall
    'win_requirements': f"""
        {coldef('id')},
        item_id integer,
        quantity float(4),
        char_id integer,
        loc_id integer,
        attrib_id integer,
        attrib_value float(4),
        UNIQUE (game_token, item_id, char_id, loc_id, attrib_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    }

