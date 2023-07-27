"""
Relation tables must be created after base tables because
the keys depend on their prior existence.
"""
from .db_serializable import coldef

tables_to_create = {
    # Character
    'char_attribs': f"""
        {coldef('game_token')},
        char_id INTEGER,
        attrib_id INTEGER,
        PRIMARY KEY (game_token, char_id, attrib_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
    """,
    'char_items': f"""
        {coldef('game_token')},
        char_id INTEGER,
        item_id INTEGER,
        PRIMARY KEY (game_token, char_id, item_id),
        FOREIGN KEY (game_token, char_id)
            REFERENCES characters (game_token, id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
    """,
    # Item
    'item_attribs': f"""
        {coldef('game_token')},
        item_id INTEGER,
        attrib_id INTEGER,
        PRIMARY KEY (game_token, item_id, attrib_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attribs (game_token, id)
    """,
    'item_sources': f"""
        {coldef('game_token')},
        item_id INTEGER,
        source_id INTEGER,
        PRIMARY KEY (game_token, item_id, source_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id),
        FOREIGN KEY (game_token, source_id)
            REFERENCES items (game_token, id)
    """,
    # Location
    'location_destinations': f"""
        {coldef('game_token')},
        origin_id INTEGER,
        dest_id INTEGER,
        PRIMARY KEY (game_token, origin_id, dest_id),
        FOREIGN KEY (game_token, origin_id)
            REFERENCES locations (game_token, id),
        FOREIGN KEY (game_token, dest_id)
            REFERENCES locations (game_token, id)
    """,
    # Overall
    'winning_items': f"""
        {coldef('game_token')},
        item_id INTEGER,
        quantity INTEGER NOT NULL,
        PRIMARY KEY (game_token, item_id),
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
    """,
}

