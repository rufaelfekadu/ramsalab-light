import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))
    FLASK_PORT = os.environ.get("FLASK_PORT", 5000)
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "_uploads")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
    ALLOWED_EXTENSIONS = {".wav", ".mp3", ".webm"}
    LOG_FOLDER = os.environ.get("LOG_FOLDER", "logs")
    LOG_FILE = os.environ.get("LOG_FILE", "audio_submissions.log")
    WTF_CSRF_ENABLED = True  # Enable CSRF protection

    # session configuration
    SESSION_PERMANENT = False

    # PostgreSQL connection string
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = os.environ.get("DB_PORT", "5432")
    DB_NAME = os.environ.get("DB_NAME", "mbzuai_db")
    DB_USER = os.environ.get("DB_USER", "admin")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_URI = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    # SQLAlchemy configuration
    SQLALCHEMY_DATABASE_URI = DB_URI
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # WhatsApp API configuration
    WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "your_token_here")
    WHATSAPP_URL = os.environ.get("WHATSAPP_URL", "https://graph.facebook.com/v17.0/")
    WHATSAPP_FROM_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "your_phone_number_id_here")
    WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
    WHATSAPP_WEBHOOK_ENDPOINT = os.environ.get("WHATSAPP_WEBHOOK_ENDPOINT", "https://mbzsurvey.dev/whatsapp-webhook-endpoint")
