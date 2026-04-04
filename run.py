import logging
import os
from app import create_app
from app.database import start_postgres

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = create_app()

if __name__ == "__main__":
    # The host='0.0.0.0' allows it to be seen on your local network if needed
    start_postgres()
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
