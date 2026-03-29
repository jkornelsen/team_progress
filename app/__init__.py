import os
import logging
import uuid
from flask import Flask, g, session, request, redirect, url_for
from flask_migrate import Migrate

from .models import db, GENERAL_ID
from .serialization import init_game_session
from .utils import format_num, htmlify_filter, mask_string
from app.src.routes_session import session_bp, generate_username, log_user_activity
from app.src.routes_configure import configure_bp
from app.src.routes_play import play_bp

def create_app():
    app = Flask(__name__)

    # ------------------------------------------------------------------------
    # 1. Configuration
    # ------------------------------------------------------------------------
    app.config['TITLE'] = 'Team Progress Kit'
    app.config['SECRET_KEY'] = os.environ.get(
        'SECRET_KEY', 'team-progress-kit')
    app.config['DATA_DIR'] = os.path.join(app.root_path, 'data_files')
    
    # Database Configuration (PostgreSQL)
    # 1. Try to get password from sensitive.py
    try:
        from .sensitive import DB_PASSWORD
    except (ImportError, ValueError):
        # Fallback for local trusted authentication
        DB_PASSWORD = 'no password needed with trust'

    # 2. Get other connection details from Environment or Defaults
    db_user = os.environ.get('DB_USER', 'postgres')
    db_pass = os.environ.get('DB_PASS', DB_PASSWORD) 
    db_host = os.environ.get('DB_HOST', 'localhost')
    db_name = os.environ.get('DB_NAME', 'app')
    db_port = os.environ.get('DB_PORT', '5432')
    
    # 3. Construct the SQLAlchemy URI
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}")
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.config['UPLOAD_DIR'] = os.path.join(app.config['DATA_DIR'], 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

    # ------------------------------------------------------------------------
    # 2. Extensions Initialization
    # ------------------------------------------------------------------------
    db.init_app(app)
    Migrate(app, db)

    # ------------------------------------------------------------------------
    # 3. Blueprints Registration
    # ------------------------------------------------------------------------
    app.register_blueprint(session_bp)
    app.register_blueprint(configure_bp)
    app.register_blueprint(play_bp)

    # ------------------------------------------------------------------------
    # 4. Filters & Context Processors (UI Support)
    # ------------------------------------------------------------------------
    @app.template_filter('formatNum')
    def filter_format_num(value):
        return format_num(value, g.get('number_format', 'en_US'))

    @app.template_filter('htmlify')
    def filter_htmlify(html):
        return htmlify_filter(html)

    @app.template_filter('mask_string')
    def filter_mask_string(s):
        return mask_string(s)

    @app.context_processor
    def inject_user_vars():
        return {
            'current_username': session.get('username'),
            'game_token': session.get('game_token')
        }

    @app.context_processor
    def inject_globals():
        return {'GENERAL_ID': GENERAL_ID}

    # ------------------------------------------------------------------------
    # 5. Middleware (Session & Multi-tenancy)
    # ------------------------------------------------------------------------
    @app.before_request
    def initialize_session():
        """
        The core multi-tenant logic. 
        Ensures every request has a game_token and an initialized System ID 1.
        """
        if request.endpoint and (request.endpoint.startswith('static') or 'favicon' in request.endpoint):
            return

        # 1. Ensure Game Token exists in session
        if 'game_token' not in session:
            session['game_token'] = str(uuid.uuid4())
        
        # Set global game token for use in SQLAlchemy queries
        g.game_token = session['game_token']

        # 2. Bootstrap the game session (Ensure ID 1 exists)
        # This is a lightweight check performed via database_setup.py
        init_game_session()

        # 3. Ensure User Identity
        if 'username' not in session:
            session['username'] = generate_username()

        # 4. Log the user's presence for 'Session Users' view
        if request.endpoint:
            # Avoid logging purely technical/api redirects
            if not any(x in request.endpoint for x in ['log_visit', 'status']):
                entity_id = request.view_args.get('id') if request.view_args else None
                log_user_activity(request.endpoint, entity_id)

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        """Ensures database connections are returned to the pool."""
        db.session.remove()

    # ------------------------------------------------------------------------
    # 6. Default Routes
    # ------------------------------------------------------------------------
    @app.route('/')
    def root():
        return redirect(url_for('play.overview'))

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
