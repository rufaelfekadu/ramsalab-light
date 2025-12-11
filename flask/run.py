from dotenv import load_dotenv
from app import create_app

# Load environment variables from .env file
load_dotenv()

app = create_app()

if __name__ == "__main__":
    import os
    port = app.config['FLASK_PORT']
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    app.run(debug=debug, host="0.0.0.0", port=port)
