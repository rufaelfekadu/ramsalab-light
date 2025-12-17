"""
Helper functions for route handlers to eliminate code duplication.
"""
import os
from flask import current_app, jsonify, session, request
from flask_login import current_user

from app.database import db
from app.models import User, Response, Progress
from app.whatsapp_utils import WhatsAppClient


def get_user_from_request():
    """
    Get user from current_user (if authenticated) or from session.
    
    Returns:
        User object if found, None otherwise
    """
    if current_user.is_authenticated:
        return current_user
    
    user_id = session.get("user_id")
    if user_id:
        try:
            user_id = int(user_id)
            return User.query.filter_by(id=user_id).first()
        except (ValueError, TypeError):
            return None
    
    return None


def validate_and_get_user_id():
    """
    Validate and get user_id from request (form, JSON, or args).
    
    Returns:
        tuple: (user_id_int, error_response) or (None, None) if not found
        error_response is a JSON response tuple (jsonify(...), status_code) or None
    """
    user_id = (request.form.get("user_id") or 
               (request.json.get("user_id") if request.is_json else None) or
               request.args.get("user_id"))
    
    if not user_id:
        return None, (jsonify({"status": "error", "message": "User ID is required"}), 400)
    
    try:
        user_id_int = int(user_id)
        return user_id_int, None
    except (ValueError, TypeError):
        return None, (jsonify({"status": "error", "message": "Invalid user ID format"}), 400)


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
    1. First tries to get user from session (if not authenticated)
    2. If no user found, creates a new anonymous user with token
    3. Sets session['user_id'] if a new user is created
    4. Uses create_new_user_token() to generate an 8-character username prefix
    
    Returns:
        User object (never None - always creates if needed)
    """
    # First check if user is authenticated
    if current_user.is_authenticated:
        return current_user
    
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


def delete_response_files(responses, upload_folder):
    """
    Delete files associated with responses.
    
    Args:
        responses: List of Response objects
        upload_folder: Base upload folder path from config
    
    Returns:
        int: Number of files successfully deleted
    """
    deleted_count = 0
    for response in responses:
        if response.file_path and not response.file_path.startswith('http'):
            try:
                # Handle both absolute and relative paths
                if os.path.isabs(response.file_path):
                    file_path = response.file_path
                else:
                    # If it's a relative path, construct it from UPLOAD_FOLDER
                    file_path = os.path.join(upload_folder, 
                                           str(response.question_id), 
                                           os.path.basename(response.file_path))
                if os.path.exists(file_path):
                    os.remove(file_path)
                    deleted_count += 1
                    current_app.logger.info(f'Deleted file: {file_path}')
            except Exception as e:
                current_app.logger.warning(f"Could not delete file {response.file_path}: {e}")
    
    return deleted_count


def send_whatsapp_deletion_notification(phone_number, user_id):
    """
    Send WhatsApp notification when user data is deleted.
    
    Args:
        phone_number: User's phone number
        user_id: User ID for logging
    
    Returns:
        bool: True if notification sent successfully, False otherwise
    """
    if not phone_number:
        return False
    
    try:
        whatsapp_client = WhatsAppClient()
        deletion_message = "Your data has been deleted from our system. Thank you for your participation."
        message_response = whatsapp_client.send_text_message(phone_number, deletion_message)
        if whatsapp_client.is_message_sent_successfully(message_response):
            current_app.logger.info(f'Sent deletion notification to {phone_number} for user {user_id}')
            return True
        else:
            current_app.logger.warning(f'Failed to send deletion notification to {phone_number}: {message_response.status_code} - {message_response.text}')
            return False
    except Exception as e:
        # Don't fail the deletion if WhatsApp message fails
        current_app.logger.error(f'Error sending WhatsApp deletion notification: {e}', exc_info=True)
        return False

