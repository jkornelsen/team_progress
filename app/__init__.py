import os
from datetime import timedelta
from flask import (
    Flask, g, session, request, redirect, render_template, url_for)
from flask_migrate import Migrate
from jinja2 import StrictUndefined

from app.src.logic_user_interaction import generate_username, log_activity
from app.src.routes_session import session_bp
from app.src.routes_configure import configure_bp
from app.src.routes_play import play_bp
from .database import db, get_db_uri
from .models import GENERAL_ID, EQUIPMENT_SLOTS_ID, StorageType
from .utils import format_num, htmlify_filter, mask_string

def create_app():
    app = Flask(__name__)

    # ------------------------------------------------------------------------
    # 1. Configuration
    # ------------------------------------------------------------------------
    app.config['TITLE'] = 'Team Progress Kit'
    app.config['SECRET_KEY'] = os.environ.get(
        'SECRET_KEY', 'team-progress-kit')
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    app.config['DATA_DIR'] = os.path.join(app.root_path, 'data_files')

    app.config['SQLALCHEMY_DATABASE_URI'] = get_db_uri()
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'timeout': 15
        }
    }

    app.config['UPLOAD_DIR'] = os.path.join(app.config['DATA_DIR'], 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

    # ------------------------------------------------------------------------
    # 2. Extensions Initialization
    # ------------------------------------------------------------------------
    db.init_app(app)
    Migrate(app, db)
    app.jinja_env.undefined = StrictUndefined

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
    def filter_htmlify(html, allow_links=True):
        return htmlify_filter(html, allow_links)

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
        return {
            'GENERAL_ID': GENERAL_ID,
            'EQUIPMENT_SLOTS_ID': EQUIPMENT_SLOTS_ID,
            'StorageType': StorageType
        }

    # ------------------------------------------------------------------------
    # 5. Middleware (Session & Multi-tenancy)
    # ------------------------------------------------------------------------
    @app.before_request
    def initialize_session():
        """
        Lightweight per-request setup.

        Deliberately does not create a game_token or a Scenario db record.
        That only happens when a route opts in via ensure_game_token(),
        typically when the user starts editing a scenario or loads one.
        """
        if request.endpoint and (
                request.endpoint.startswith('static')
                or 'favicon' in request.endpoint):
            return

        # Attach any existing game token to this request, if present
        g.game_token = session.get('game_token')
        if g.game_token:
            session.permanent = True # keep for PERMANENT_SESSION_LIFETIME

        # User Settings
        if 'username' not in session:
            session['username'] = generate_username()
        if 'number_format' not in session:
            session['number_format'] = 'en_US'
        g.number_format = session['number_format']

        # Log the user's presence for 'Session Users' view
        if request.endpoint:
            # Avoid logging purely technical/api redirects
            if not any(x in request.endpoint for x in ['log_visit', 'status']):
                entity_id = request.view_args.get('id') if request.view_args else None
                log_activity(request.endpoint, entity_id)

    @app.teardown_appcontext
    def shutdown_session(_exception=None):
        """Ensures database connections are returned to the pool."""
        db.session.remove()

    # ------------------------------------------------------------------------
    # 6. Default Routes
    # ------------------------------------------------------------------------
    @app.route('/')
    def root():
        return redirect(url_for('play.overview'))

    @app.errorhandler(404)
    def page_not_found(_e):
        return render_template(
            'error.html',
            message="404 Not Found",
            details="Perhaps you need to go back and reload the page.",
        ), 404

    return app

if __name__ == '__main__':
    flask_app = create_app()
    flask_app.run(debug=True)
