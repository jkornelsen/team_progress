import logging
import os
import argparse
from app import create_app
from app.database import start_postgres

def main():
    parser = argparse.ArgumentParser(description="Run the Team Progress Kit server.")
    parser.add_argument(
        '--log', 
        nargs='?',
        default='WARNING',
        const='WARNING',
        help='Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).'
    )
    parser.add_argument(
        '--nodebug', 
        action='store_false', 
        dest='debug', 
        help='Disable Flask debug mode.'
    )
    parser.set_defaults(debug=True)
    args = parser.parse_args()

    # 1. Setup Logging
    numeric_level = getattr(logging, args.log.upper(), None)
    if not isinstance(numeric_level, int):
        print(f"Invalid log level: {args.log}")
        numeric_level = logging.INFO

    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Silence noisy library logs
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    # 2. Initialize App
    app = create_app()

    # 3. Start Database and App
    start_postgres()
    print(f"Starting app in {'DEBUG' if args.debug else 'PRODUCTION'} mode at level {args.log.upper()}")
    app.run(host='127.0.0.1', port=5000, debug=args.debug, use_reloader=False)

if __name__ == "__main__":
    main()
