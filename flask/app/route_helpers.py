"""
Helper functions for route handlers to eliminate code duplication.
"""
from flask import current_app, session

from app.database import db
from app.models import User


def get_user_from_request():
    """
    Get user from session.
    
    Returns:
        User object if found, None otherwise
    """
    user_id = session.get("user_id")
    if user_id:
        try:
            user_id = int(user_id)
            return User.query.filter_by(id=user_id).first()
        except (ValueError, TypeError):
            return None
    
    return None


def create_new_user_token():
    """
    Create a new unique user token (UUID).
    This function is used by get_or_create_anonymous_user and other places.
    
    Returns:
        str: A unique UUID token string
    """
    import uuid
    token = str(uuid.uuid4())
    # Ensure token is unique
    while User.query.filter_by(token=token).first():
        token = str(uuid.uuid4())
    return token


def generate_unique_deletion_token():
    """
    Generate a unique 6-digit deletion token for data deletion requests.
    This function generates a random 6-digit number (100000-999999) and ensures
    it's unique by checking against existing delete_data_token values.
    
    Returns:
        str: A unique 6-digit string (e.g., "012345", "999999")
    """
    import random
    max_attempts = 100  # Prevent infinite loop in edge case
    
    for attempt in range(max_attempts):
        # Generate random 6-digit number (100000-999999)
        token_int = random.randint(100000, 999999)
        token_str = f"{token_int:06d}"  # Format with leading zeros
        
        # Check if token already exists
        existing_user = User.query.filter_by(delete_data_token=token_str).first()
        if not existing_user:
            return token_str
    
    # If we've exhausted attempts (extremely unlikely), raise an error
    raise RuntimeError("Failed to generate unique deletion token after maximum attempts")


def get_or_create_anonymous_user():
    """
    Get user from session or create a new anonymous user.
    This function:
    1. Tries to get user from session
    2. If no user found, creates a new anonymous user with token
    3. Sets session['user_id'] if a new user is created
    4. Uses create_new_user_token() to generate an 8-character username prefix
    
    Returns:
        User object (never None - always creates if needed)
    """
    # Try to get user from session
    user_id = session.get("user_id")
    if user_id:
        try:
            user_id = int(user_id)
            user = User.query.filter_by(id=user_id).first()
            if user:
                # Ensure token exists
                if not user.token:
                    user.token = create_new_user_token()
                    db.session.commit()
                return user
        except (ValueError, TypeError):
            pass
    
    # Create new anonymous user with token
    token = create_new_user_token()
    user = User(username=f"user_{token[:8]}", token=token)
    db.session.add(user)
    db.session.flush()  # Get the user ID
    session['user_id'] = user.id
    db.session.commit()
    current_app.logger.info(f'Created new anonymous user {user.id} with token (username: {user.username})')
    
    return user

