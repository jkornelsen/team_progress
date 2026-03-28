from app import create_app

app = create_app()

if __name__ == "__main__":
    # The host='0.0.0.0' allows it to be seen on your local network if needed
    app.run(
        debug=True, use_reloader=False, use_debugger=False,
        host='127.0.0.1', port=5000)
