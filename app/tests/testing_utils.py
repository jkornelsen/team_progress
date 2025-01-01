from flask import g
from app import app
from database import set_default_schema

def configure_app():
    app.config['TESTING'] = True

def push_app_context():
    """Push the app context to allow access to global objects like 'g'."""
    app_context = app.app_context()
    # Push the app context onto the context stack,
    # making it available for the current thread.
    app_context.push()
    return app_context

def set_schema():
    """Set the schema for actual database connections.
    Call push_app_context() first for g.
    Not needed when mocking db.
    """
    set_default_schema('testing')

def init_test_client():
    """Initialize and return the Flask test client to simulate requests and
    trigger before_request hooks.
    """
    client = app.test_client()
    client.get('/')  # Trigger a GET request.
    return client

def setup_with_db():
    configure_app()
    app_context = push_app_context()
    set_schema()
    client = init_test_client()
    return app_context, client
