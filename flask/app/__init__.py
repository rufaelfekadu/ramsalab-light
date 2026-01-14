import os
import logging
from flask import Flask
from config import Config
from flask_wtf import CSRFProtect
from flask_login import LoginManager
from dotenv import load_dotenv

csrf = CSRFProtect()
login_manager = LoginManager()

def create_app():
    load_dotenv()
    
    app = Flask(__name__)
    app.config.from_object(Config)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Logging configuration - both file and stdout
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    
    # File handler
    os.makedirs(app.config["LOG_FOLDER"], exist_ok=True)
    file_handler = logging.FileHandler(app.config["LOG_FILE"])
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    
    # Console handler (stdout)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Init database
    from app.database import init_app as init_db
    init_db(app)

    # Init security (CSRF protection still needed for forms)
    csrf.init_app(app)

    # Init Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = 'routes.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    # User loader callback for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return User.query.get(int(user_id))

    # Register routes
    from app.routes import bp as routes_bp
    app.register_blueprint(routes_bp)

    return app
