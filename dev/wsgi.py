# For http://teamprogress.pythonanywhere.com/
# Located outside user dir at /var/www/teamprogress_pythonanywhere_com_wsgi.py
# Edit under "Web" rather than "Files"

import sys
import os
from dotenv import load_dotenv

project_folder = '/home/teamprogress/team_progress'
if project_folder not in sys.path:
    sys.path.append(project_folder)

load_dotenv(os.path.join(project_folder, '.env'))

from app import create_app
application = create_app()
