import unittest
from flask import g
from app import create_app, db
from app.serialization import init_game_session

class BaseTestCase(unittest.TestCase):
    def setUp(self):
        """Sets up an in-memory database and a test client."""
        self.app = create_app()
        self.app.config['TESTING'] = True
        # Use SQLite in-memory for lightning fast tests
        self.app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite://"
        
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Create all tables in the in-memory DB
        db.create_all()
        
        # Bootstrap the session
        self.game_token = "test-token-123"
        g.game_token = self.game_token 
        init_game_session()

    def tearDown(self):
        """Cleans up the database and context."""
        db.session.remove()
        db.drop_all()
        self.app_context.pop()
