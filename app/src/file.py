import os
from flask import (
    g,
    json,
    redirect,
    send_file,
    session,
    url_for
)
from tkinter import Tk, filedialog

from src.game_data import GameData

FILE_DIR = 'data'
MAX_FILENAME_LENGTH = 30

def generate_filename(title):
    # Remove special characters and replace with '_'
    filename = ''.join(c if c.isalnum() else '_' for c in title)
    filename = filename[:MAX_FILENAME_LENGTH]  # Limit filename length
    filename = "{}.json".format(filename)
    return filename

def get_filepath(filename):
    return os.path.join(FILE_DIR, filename)

def set_routes(app):
    @app.route('/save_to_file')
    def save_to_file():
        data_to_save = g.game_data.to_json()
        filename = generate_filename(g.game_data.overall.title)
        filepath = get_filepath(filename)
        with open(filepath, 'w') as outfile:
            json.dump(data_to_save, outfile, indent=4)
        return send_file(filepath, as_attachment=True)
        #session['file_message'] = 'Saved to file.'
        #return redirect(url_for('configure'))

    @app.route('/load_from_file')
    def load_from_file():
        root = Tk()
        root.withdraw()
        filepath = filedialog.askopenfilename()
        if not filepath:
            # No file selected
            return redirect(url_for('configure'))
        with open(filepath, 'r') as infile:
            data = json.load(infile)
            g.game_data = GameData.from_json(data)
        g.game_data.to_db()
        session['file_message'] = 'Loaded from file.'
        return redirect(url_for('configure'))

