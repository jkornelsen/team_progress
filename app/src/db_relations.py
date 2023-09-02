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
        value integer NOT NULL,
        PRIMARY KEY (game_token, item_id, attrib_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
    """,
    'recipes': f"""
        {coldef('game_token')},
        item_id integer NOT NULL,
        recipe_id integer NOT NULL,
        rate_amount integer NOT NULL,
        rate_duration float(2) NOT NULL,
        instant boolean NOT NULL,
        PRIMARY KEY (game_token, item_id, recipe_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
    """,
    'recipe_sources': f"""
        {coldef('game_token')},
        item_id integer NOT NULL,
        recipe_id integer NOT NULL,
        source_id integer NOT NULL,
        quantity integer NOT NULL,
        preserve boolean NOT NULL,
        PRIMARY KEY (game_token, item_id, recipe_id, source_id)
    """,
    # Character
    'char_attribs': f"""
        {coldef('game_token')},
        char_id integer NOT NULL,
        attrib_id integer NOT NULL,
        value integer NOT NULL,
        PRIMARY KEY (game_token, char_id, attrib_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
    """,
    'char_items': f"""
        {coldef('game_token')},
        char_id integer NOT NULL,
        item_id integer NOT NULL,
        quantity integer NOT NULL,
        slot varchar(50),
        PRIMARY KEY (game_token, char_id, item_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
    """,
    # Location
    'loc_destinations': f"""
        {coldef('game_token')},
        loc_id integer NOT NULL,
        dest_id integer NOT NULL,
        distance integer NOT NULL,
        PRIMARY KEY (game_token, loc_id, dest_id),
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
    """,
    'loc_items': f"""
        {coldef('game_token')},
        loc_id integer NOT NULL,
        item_id integer NOT NULL,
        quantity integer NOT NULL,
        position integer[2],
        PRIMARY KEY (game_token, loc_id, item_id),
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
    """,
    # Event
    'event_attribs': f"""
        {coldef('game_token')},
        event_id integer NOT NULL,
        attrib_id integer NOT NULL,
        determining boolean NOT NULL,
        PRIMARY KEY (game_token, event_id, attrib_id),
        FOREIGN KEY (game_token, event_id)
            REFERENCES events (game_token, id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
    """,
    'event_triggers': f"""
        {coldef('game_token')},
        event_id integer NOT NULL,
        item_id integer,
        loc_id integer,
        UNIQUE (game_token, event_id, item_id, loc_id),
        FOREIGN KEY (game_token, event_id)
            REFERENCES events (game_token, id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id),
        FOREIGN KEY (game_token, loc_id)
            REFERENCES locations (game_token, id)
    """,
    # Overall
    'win_requirements': f"""
        {coldef('game_token')},
        item_id integer,
        quantity integer,
        char_id integer,
        loc_id integer,
        attrib_id integer,
        attrib_value integer,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
    """,
}

