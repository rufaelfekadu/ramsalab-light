import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app, jsonify, g
from app.models import Response, SurveyLogic, Survey
import json
import boto3
from botocore.exceptions import ClientError
from datetime import timedelta
from io import BytesIO

def allowed_file(filename: str) -> bool:
    """Check if the file extension is allowed."""
    if not filename:
        return False
    return os.path.splitext(filename)[1].lower() in current_app.config["ALLOWED_EXTENSIONS"]


def _is_spaces_enabled() -> bool:
    """
    Check if DigitalOcean Spaces is configured and enabled.
    Caches result in Flask's g to avoid repeated config checks.
    
    Returns:
        True if Spaces is enabled and configured, False otherwise
    """
    if not hasattr(g, '_spaces_enabled'):
        use_spaces = current_app.config.get('USE_SPACES', False)
        spaces_key = current_app.config.get('SPACES_KEY', '').strip()
        spaces_bucket = current_app.config.get('SPACES_BUCKET', '').strip()
        g._spaces_enabled = bool(use_spaces and spaces_key and spaces_bucket)
    return g._spaces_enabled


def _generate_safe_filename(user_id: str, original_filename: str = None, extension: str = None) -> str:
    """
    Generate a safe, unique filename for uploads.
    
    Args:
        user_id: User ID for filename generation
        original_filename: Original filename (optional, for extracting extension)
        extension: File extension (optional, overrides extraction from filename)
    
    Returns:
        Safe filename string
    """
    if extension is None:
        if original_filename:
            ext = os.path.splitext(original_filename)[1].lower() or ".webm"
        else:
            ext = ".webm"
    else:
        ext = extension if extension.startswith('.') else f".{extension}"
    
    unique_id = uuid.uuid4().hex
    safe_name = secure_filename(f"{user_id}_{unique_id}{ext}")
    return safe_name


def _get_spaces_client():
    """
    Get boto3 S3 client configured for DigitalOcean Spaces.
    Uses Flask's g to cache the client per request context for efficiency.
    
    Returns:
        Cached boto3 S3 client
    """
    if not hasattr(g, '_spaces_client'):
        # Validate credentials
        spaces_key = current_app.config.get('SPACES_KEY', '').strip()
        spaces_secret = current_app.config.get('SPACES_SECRET', '').strip()
        spaces_endpoint = current_app.config.get('SPACES_ENDPOINT', '').strip()
        spaces_region = current_app.config.get('SPACES_REGION', 'nyc3').strip()
        
        if not spaces_key or not spaces_secret:
            raise ValueError("SPACES_KEY and SPACES_SECRET must be configured")
        
        if not spaces_endpoint:
            # Auto-generate endpoint if not provided
            spaces_endpoint = f"https://{spaces_region}.digitaloceanspaces.com"
        
        session = boto3.session.Session()
        g._spaces_client = session.client('s3',
            region_name=spaces_region,
            endpoint_url=spaces_endpoint,
            aws_access_key_id=spaces_key,
            aws_secret_access_key=spaces_secret)
    
    return g._spaces_client

def get_spaces_signed_url(spaces_key: str, expiration: int = 3600) -> str:
    """
    Generate a signed URL for a file in DigitalOcean Spaces.
    
    Args:
        spaces_key: The Spaces key (e.g., "123/filename.webm")
        expiration: URL expiration time in seconds (default: 1 hour)
    
    Returns:
        Signed URL string
    """
    try:
        client = _get_spaces_client()
        url = client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': current_app.config['SPACES_BUCKET'],
                'Key': spaces_key
            },
            ExpiresIn=expiration
        )
        return url
    except Exception as e:
        current_app.logger.error(f"Error generating signed URL for {spaces_key}: {e}")
        raise

def upload_file_to_spaces(local_file_path: str, question_id: str, user_id: str = None) -> str:
    """
    Upload a local file to DigitalOcean Spaces using streaming.
    
    Args:
        local_file_path: Path to the local file
        question_id: Question ID for organizing files
        user_id: Optional user ID for filename generation
    
    Returns:
        Spaces key (e.g., "123/filename.webm")
    """
    if not os.path.exists(local_file_path):
        raise FileNotFoundError(f"Local file not found: {local_file_path}")
    
    # Generate unique filename using centralized helper
    filename = os.path.basename(local_file_path)
    ext = os.path.splitext(filename)[1].lower()
    if user_id:
        safe_name = _generate_safe_filename(user_id, filename, ext)
    else:
        # Use original filename with unique prefix
        unique_id = uuid.uuid4().hex
        safe_name = secure_filename(f"{unique_id}_{filename}")
    
    spaces_key = f"{question_id}/{safe_name}"
    client = _get_spaces_client()
    
    try:
        # Stream file directly to Spaces without loading into memory
        with open(local_file_path, 'rb') as f:
            client.upload_fileobj(f,
                                current_app.config['SPACES_BUCKET'],
                                spaces_key,
                                ExtraArgs={'ACL': 'private'})
        
        current_app.logger.info(f"Uploaded file to Spaces: {local_file_path} -> {spaces_key}")
        return spaces_key
    except ClientError as e:
        current_app.logger.error(f"Error uploading file to Spaces: {e}")
        raise

def save_audio_file(audio, user_id: str, question_id: str) -> str:
    """
    Save audio file securely. Uses Spaces if configured, otherwise saves locally.
    Optimized to stream directly without double buffering.
    
    Args:
        audio: File-like object (Werkzeug FileStorage or similar)
        user_id: User ID for filename generation
        question_id: Question ID for organizing files
    
    Returns:
        Spaces key (if using Spaces) or filename (if local)
    """
    # Validate file extension first
    filename = audio.filename or "recording.webm"
    ext = os.path.splitext(filename)[1].lower() or ".webm"
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ValueError(f"Invalid file extension: {ext}. Allowed: {current_app.config['ALLOWED_EXTENSIONS']}")
    
    # Generate safe filename once
    safe_name = _generate_safe_filename(user_id, filename, ext)
    
    # Try Spaces upload first if enabled
    if _is_spaces_enabled():
        try:
            client = _get_spaces_client()
            spaces_key = f"{question_id}/{safe_name}"
            
            # Stream directly from file object to Spaces (no double buffering)
            # Reset file pointer to beginning
            audio.seek(0)
            client.upload_fileobj(audio,
                                current_app.config['SPACES_BUCKET'],
                                spaces_key,
                                ExtraArgs={'ACL': 'private'})
            
            current_app.logger.info("user_id=%s, question_id=%s, file=%s, spaces_key=%s", 
                                   user_id, question_id, safe_name, spaces_key)
            return spaces_key
        except Exception as e:
            current_app.logger.error(f"Failed to upload to Spaces, falling back to local: {e}")
            # Reset file pointer for local fallback
            audio.seek(0)
    
    # Local storage fallback
    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], str(question_id))
    os.makedirs(save_path, exist_ok=True)
    filepath = os.path.join(save_path, safe_name)
    
    # Stream file to disk (read in chunks for large files, though max is 16MB)
    audio.seek(0)
    with open(filepath, 'wb') as f:
        # Read in chunks to handle large files efficiently
        chunk_size = 8192  # 8KB chunks
        while True:
            chunk = audio.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
    
    # Log submission
    current_app.logger.info("user_id=%s, question_id=%s, file=%s", user_id, question_id, safe_name)
    
    return safe_name

def _create_web_response(user, current_question, request):
    """
    Create a response object for web interface responses
    Handles field names in format: question_{question_id}_{field_name}
    """
    # Use last_question_asked if available, otherwise fall back to current_question.id
    question_id = user.last_question_asked if user.last_question_asked else current_question.id
    question_prefix = f"question_{question_id}_"
    
    if current_question.question_type == "audio":
        audio = request.files.get(f"{question_prefix}audio")
        if not audio:
            # Also try the old format for backward compatibility
            audio = request.files.get("audio")
        if not audio:
            return None  # Return None instead of jsonify error, let route handle it
        if not allowed_file(audio.filename):
            return None
        
        file_identifier = save_audio_file(audio, user.id, str(current_question.id))
        response_value = file_identifier
        
        # Determine file_path based on whether we're using Spaces or local storage
        if _is_spaces_enabled():
            # file_identifier is a Spaces key (e.g., "123/filename.webm")
            file_path = file_identifier
        else:
            # file_identifier is a filename, construct local path
            file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], str(current_question.id), file_identifier)
        
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type=current_question.question_type,
            response_value=response_value,
            file_path=file_path
        )
    elif current_question.question_type == "text":
        response_value = request.form.get(f"{question_prefix}response_text", "").strip()
        if not response_value:
            # Also try the old format for backward compatibility
            response_value = request.form.get("response_text", "").strip()
        if not response_value:
            return None  # Return None if no response, let route handle validation
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type=current_question.question_type,
            response_value=response_value
        )
    elif current_question.question_type == "interactive":
        selected_option = request.form.get(f"{question_prefix}selected_option")
        selected_options = request.form.getlist(f"{question_prefix}selected_options")
        
        # Also try the old format for backward compatibility
        if not selected_option:
            selected_option = request.form.get("selected_option")
        if not selected_options:
            selected_options = request.form.getlist("selected_options")
        
        if selected_option:
            return Response(
                user_id=user.id,
                question_id=question_id,
                response_type=current_question.question_type,
                response_value=selected_option
            )
        elif selected_options:
            return Response(
                user_id=user.id,
                question_id=question_id,
                response_type=current_question.question_type,
                response_value=json.dumps(selected_options)
            )
        else:
            # Return None if no response provided
            return None
    return None

def _handle_web_survey_logic(survey_name, current_question, response_value, response_type):
    """
    Handle survey logic for web interface responses
    
    Args:
        survey_name: Name of the survey
        current_question: Current question object
        response_value: The response value from the user
        response_type: Type of response (radio, checkbox, text, audio)
    
    Returns:
        Next prompt number (may be modified by logic)
    """
    try:
        # Look up survey by name to get survey_id
        survey = Survey.query.filter_by(name=survey_name).first()
        if not survey:
            current_app.logger.warning(f'Survey {survey_name} not found for logic check')
            return current_question.prompt_number + 1

        if response_type in ["radio", "checkbox"]:
            # For interactive responses, check survey logic
            current_app.logger.debug(f'Checking logic for survey {survey_name}, question {current_question.id}, response {response_value}')
            
            # Parse response_value for checkbox (JSON array) or use directly for radio
            if response_type == "checkbox":
                import json
                try:
                    response_options = json.loads(response_value)
                    # For checkbox, we'll check logic for each selected option
                    for option in response_options:
                        logic = SurveyLogic.query.filter_by(
                            survey_id=survey.id,
                            question_id=current_question.id,
                            response_option_id=option
                        ).first()
                        if logic and logic.next_question:
                            next_prompt = logic.next_question.prompt_number
                            current_app.logger.info(f'Found logic for checkbox option {option}: jumping to prompt {next_prompt}')
                            return next_prompt
                except json.JSONDecodeError:
                    pass
            else:
                # For radio button responses
                logic = SurveyLogic.query.filter_by(
                    survey_id=survey.id,
                    question_id=current_question.id,
                    response_option_id=response_value
                ).first()
                
                if logic and logic.next_question:
                    next_prompt = logic.next_question.prompt_number
                    current_app.logger.info(f'Found logic for radio option {response_value}: jumping to prompt {next_prompt}')
                    return next_prompt
                    
    except Exception as e:
        current_app.logger.error(f'Error checking web survey logic: {e}', exc_info=True)
    
    # Default: move to next question
    return current_question.prompt_number + 1


