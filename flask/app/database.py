"""
Database configuration and models using SQLAlchemy
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# Initialize extensions
db = SQLAlchemy()

def init_app(app: Flask):
    """Initialize database with Flask app"""
    db.init_app(app)
    
    # Import models after db is initialized
    from app.models import User, Question, Response
