"""
Relation tables must be created after base tables because
the keys depend on their prior existence.
"""
from .db_serializable import coldef

tables_to_create = {
    # Character
    'char_attribs': f"""
        {coldef('game_token')},
        char_id integer,
        attrib_id integer,
        value integer,
        PRIMARY KEY (game_token, char_id, attrib_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
    """,
    'char_items': f"""
        {coldef('game_token')},
        char_id integer,
        item_id integer,
        quantity integer NOT NULL,
        PRIMARY KEY (game_token, char_id, item_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
    """,
    # Item
    'item_attribs': f"""
        {coldef('game_token')},
        item_id integer,
        attrib_id integer,
        value integer,
        PRIMARY KEY (game_token, item_id, attrib_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
    """,
    'item_sources': f"""
        {coldef('game_token')},
        item_id integer,
        recipe_id integer,
        source_id integer,
        src_qty integer NOT NULL,
        rate_amount integer NOT NULL,
        rate_duration float(2) NOT NULL,
        instant boolean,
        PRIMARY KEY (game_token, item_id, recipe_id, source_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id),
        FOREIGN KEY (game_token, source_id)
            REFERENCES items (game_token, id)
    """,
    # Location
    'location_destinations': f"""
        {coldef('game_token')},
        origin_id integer,
        dest_id integer,
        distance integer NOT NULL,
        PRIMARY KEY (game_token, origin_id, dest_id),
        FOREIGN KEY (game_token, origin_id)
            REFERENCES locations (game_token, id),
        FOREIGN KEY (game_token, dest_id)
            REFERENCES locations (game_token, id)
    """,
    'loc_items': f"""
        {coldef('game_token')},
        location_id integer,
        item_id integer,
        quantity integer NOT NULL,
        position integer[2],
        PRIMARY KEY (game_token, location_id, item_id),
        FOREIGN KEY (game_token, location_id)
            REFERENCES locations (game_token, id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
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
        PRIMARY KEY (game_token, item_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
    """,
}

