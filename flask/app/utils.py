import os
import uuid
from werkzeug.utils import secure_filename
from flask import current_app
import boto3
from botocore.exceptions import ClientError

def allowed_file(filename: str) -> bool:
    """Check if the file extension is allowed."""
    if not filename:
        return False
    return os.path.splitext(filename)[1].lower() in current_app.config["ALLOWED_EXTENSIONS"]


def save_audio_file(audio, user_id: str, question_id: str) -> str:
    """
    Save audio file to S3 (if enabled) or locally as fallback.
    Returns S3 URL if successful, or local file path if fallback.
    """
    # Handle case where filename might be None (e.g., from Blob)
    filename = audio.filename or "recording.webm"
    ext = os.path.splitext(filename)[1].lower() or ".webm"
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ValueError(f"Invalid file extension: {ext}. Allowed: {current_app.config['ALLOWED_EXTENSIONS']}")

    unique_id = uuid.uuid4().hex
    safe_name = secure_filename(f"{user_id}_{unique_id}{ext}")
    
    # Try S3 upload if enabled
    if current_app.config.get("AWS_S3_ENABLED", False):
        try:
            s3_key = f"{question_id}/{safe_name}"
            s3_client = boto3.client(
                's3',
                aws_access_key_id=current_app.config.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=current_app.config.get("AWS_SECRET_ACCESS_KEY"),
                region_name=current_app.config.get("AWS_REGION")
            )
            
            # Reset file pointer and upload
            audio.seek(0)
            s3_client.upload_fileobj(
                audio,
                current_app.config.get("AWS_S3_BUCKET"),
                s3_key,
                ExtraArgs={'ContentType': 'audio/webm' if ext == '.webm' else 'audio/wav' if ext == '.wav' else 'audio/mpeg'}
            )
            
            # Generate S3 URL
            s3_url = f"https://{current_app.config.get('AWS_S3_BUCKET')}.s3.{current_app.config.get('AWS_REGION')}.amazonaws.com/{s3_key}"
            current_app.logger.info("user_id=%s, question_id=%s, file=%s, s3_key=%s", 
                                   user_id, question_id, safe_name, s3_key)
            return s3_url
            
        except (ClientError, Exception) as e:
            current_app.logger.warning(f"S3 upload failed, falling back to local storage: {e}")
            # Fall through to local storage
    
    # Local storage fallback
    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], str(question_id))
    os.makedirs(save_path, exist_ok=True)
    
    filepath = os.path.join(save_path, safe_name)
    audio.seek(0)  # Reset file pointer
    audio.save(filepath)

    # Log submission
    current_app.logger.info("user_id=%s, question_id=%s, file=%s", user_id, question_id, safe_name)

    # Return relative path for local storage
    return os.path.join(str(question_id), safe_name)


