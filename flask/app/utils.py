import os
import uuid
import logging
from werkzeug.utils import secure_filename
from flask import current_app, jsonify
from app.models import Response, SurveyLogic, Survey
import json
import boto3
from botocore.exceptions import ClientError

def allowed_file(filename: str) -> bool:
    """Check if the file extension is allowed."""
    if not filename:
        return False
    return os.path.splitext(filename)[1].lower() in current_app.config["ALLOWED_EXTENSIONS"]



def save_audio_file_aws(audio, user_id: str, question_id: str) -> str:
    """Save audio file to DigitalOcean Spaces and return filename."""
    filename = audio.filename or "recording.webm"
    ext = os.path.splitext(filename)[1].lower() or ".webm"
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ValueError(f"Invalid file extension: {ext}")
    
    unique_id = uuid.uuid4().hex
    safe_name = secure_filename(f"{user_id}_{unique_id}{ext}")
    
    # Upload to Spaces
    spaces_key = f"{question_id}/{safe_name}"
    
    session = boto3.session.Session()
    client = session.client('s3',
        region_name=current_app.config['SPACES_REGION'],
        endpoint_url=current_app.config['SPACES_ENDPOINT'],
        aws_access_key_id=current_app.config['SPACES_KEY'],
        aws_secret_access_key=current_app.config['SPACES_SECRET'])
    
    try:
        audio.seek(0)  # Reset file pointer
        client.upload_fileobj(audio, 
                            current_app.config['SPACES_BUCKET'],
                            spaces_key,
                            ExtraArgs={'ACL': 'private'})
        
        # Store the Spaces URL in database
        file_url = f"{current_app.config['SPACES_ENDPOINT']}/{current_app.config['SPACES_BUCKET']}/{spaces_key}"
        
        current_app.logger.info("user_id=%s, question_id=%s, file=%s, spaces_key=%s", 
                               user_id, question_id, safe_name, spaces_key)
        return file_url  # Return URL instead of filename
    except ClientError as e:
        current_app.logger.error(f"Error uploading to Spaces: {e}")
        raise

def save_audio_file(audio, user_id: str, question_id: str) -> str:
    """Save audio file securely and return filename."""
    # Handle case where filename might be None (e.g., from Blob)
    filename = audio.filename or "recording.webm"
    ext = os.path.splitext(filename)[1].lower() or ".webm"
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ValueError(f"Invalid file extension: {ext}. Allowed: {current_app.config['ALLOWED_EXTENSIONS']}")

    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], str(question_id))
    os.makedirs(save_path, exist_ok=True)
    
    unique_id = uuid.uuid4().hex
    safe_name = secure_filename(f"{user_id}_{unique_id}{ext}")
    filepath = os.path.join(save_path, safe_name)
    audio.save(filepath)

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
        
        filename = save_audio_file(audio, user.id, str(current_question.id))
        response_value = filename
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type=current_question.question_type,
            response_value=response_value,
            file_path=os.path.join(current_app.config["UPLOAD_FOLDER"], str(current_question.id), filename)
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


