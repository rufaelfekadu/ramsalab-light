#!/usr/bin/env python3
"""
Script to clear all data from the database
"""
import os
import sys

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.database import db
from app.models import Survey, Question, QuestionGroup, SurveyLogic, User, Response, Progress
from dotenv import load_dotenv
load_dotenv()

def clear_database():
    """Clear all data from the database tables"""
    app = create_app()
    
    with app.app_context():
        print("Clearing database...")
        
        # Delete in order to respect foreign key constraints
        # Delete responses first (they reference questions and users)
        Response.query.delete()
        print("  - Cleared responses")
        
        # Delete progress entries (they reference users, surveys, and questions)
        Progress.query.delete()
        print("  - Cleared progress entries")
        
        # Delete survey logic (references surveys, questions, question_groups)
        SurveyLogic.query.delete()
        print("  - Cleared survey logic")
        
        # Delete questions (references question_groups and surveys)
        Question.query.delete()
        print("  - Cleared questions")
        
        # Delete question groups (references surveys)
        QuestionGroup.query.delete()
        print("  - Cleared question groups")
        
        # Delete surveys
        Survey.query.delete()
        print("  - Cleared surveys")
        
        # Delete users (last, as it may be referenced by responses/progress)
        User.query.delete()
        print("  - Cleared users")
        
        db.session.commit()
        print("Database cleared successfully")

if __name__ == "__main__":
    clear_database()

