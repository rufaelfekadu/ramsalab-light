"""
Utilities for exporting survey data to CSV and creating zip files with audio.
"""
import os
import csv
import tempfile
import zipfile
import shutil
from typing import List, Dict, Optional
from flask import current_app
import boto3
from botocore.exceptions import ClientError


def generate_csv(responses: List, output_path: str):
    """
    Generate CSV file with responses, demographics, and consent data.
    
    Args:
        responses: List of Response objects with joined User, Question, Survey data
        output_path: Path where CSV file should be written
    """
    fieldnames = [
        # Response metadata
        'response_id', 'user_id', 'question_id', 'question_prompt', 
        'survey_id', 'survey_name', 'response_type', 'response_value', 
        'timestamp', 'file_path', 'file_name',
        # User demographics
        'emirati_citizenship', 'age_group', 'gender', 'place_of_birth', 
        'current_residence', 'dialect_description',
        # Consent info
        'consent_read_form', 'consent_required', 'consent_optional', 
        'consent_required_2', 'consent_optional_alternative'
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for response in responses:
            user = response.user if hasattr(response, 'user') else None
            question = response.question if hasattr(response, 'question') else None
            survey = question.survey if question and hasattr(question, 'survey') else None
            
            # Extract filename and normalize file_path to match zip structure
            file_name = None
            normalized_file_path = response.file_path or ''
            
            if response.file_path:
                if response.file_path.startswith('http'):
                    # S3 URL - extract relative path (question_id/filename) and normalize
                    # URL format: https://bucket.s3.region.amazonaws.com/question_id/filename
                    url_parts = response.file_path.replace('https://', '').replace('http://', '').split('/', 1)
                    if len(url_parts) == 2:
                        relative_path = url_parts[1].split('?')[0]  # Remove query parameters
                        normalized_file_path = f"audio/{relative_path}"
                        file_name = os.path.basename(relative_path)
                else:
                    # Local path - normalize to match zip structure (audio/question_id/filename)
                    normalized_file_path = f"audio/{response.file_path}"
                    file_name = os.path.basename(response.file_path)
            
            row = {
                'response_id': response.id,
                'user_id': response.user_id,
                'question_id': response.question_id,
                'question_prompt': question.prompt if question else '',
                'survey_id': survey.id if survey else '',
                'survey_name': survey.name if survey else '',
                'response_type': response.response_type,
                'response_value': response.response_value or '',
                'timestamp': response.timestamp.isoformat() if response.timestamp else '',
                'file_path': normalized_file_path,
                'file_name': file_name or '',
                # Demographics
                'emirati_citizenship': user.emirati_citizenship if user and user.emirati_citizenship is not None else '',
                'age_group': user.age_group if user and user.age_group is not None else '',
                'gender': user.gender or '',
                'place_of_birth': user.place_of_birth or '',
                'current_residence': user.current_residence or '',
                'dialect_description': user.dialect_description or '',
                # Consent
                'consent_read_form': user.consent_read_form if user and user.consent_read_form is not None else '',
                'consent_required': user.consent_required if user and user.consent_required is not None else '',
                'consent_optional': user.consent_optional if user and user.consent_optional is not None else '',
                'consent_required_2': user.consent_required_2 if user and user.consent_required_2 is not None else '',
                'consent_optional_alternative': user.consent_optional_alternative if user and user.consent_optional_alternative is not None else '',
            }
            writer.writerow(row)


def download_audio_from_s3(s3_url: str, local_path: str) -> bool:
    """
    Download audio file from S3 to local path.
    
    Args:
        s3_url: S3 URL of the file
        local_path: Local path where file should be saved
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract S3 key from URL
        # URL format: https://bucket.s3.region.amazonaws.com/question_id/filename
        url_parts = s3_url.replace('https://', '').replace('http://', '').split('/', 1)
        if len(url_parts) != 2:
            current_app.logger.error(f"Invalid S3 URL format: {s3_url}")
            return False
        
        s3_key = url_parts[1]
        
        s3_client = boto3.client(
            's3',
            aws_access_key_id=current_app.config.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=current_app.config.get("AWS_SECRET_ACCESS_KEY"),
            region_name=current_app.config.get("AWS_REGION")
        )
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        # Download file
        s3_client.download_file(
            current_app.config.get("AWS_S3_BUCKET"),
            s3_key,
            local_path
        )
        
        current_app.logger.info(f"Downloaded S3 file {s3_key} to {local_path}")
        return True
        
    except (ClientError, Exception) as e:
        current_app.logger.error(f"Error downloading from S3: {e}")
        return False


def collect_audio_files(responses: List, temp_dir: str) -> Dict[str, str]:
    """
    Collect all audio files from responses (download from S3 or copy from local).
    Maintains the folder structure from file_path (e.g., question_id/filename).
    
    Args:
        responses: List of Response objects
        temp_dir: Temporary directory to store audio files
        
    Returns:
        Dictionary mapping response_id to local file path (relative to temp_dir)
    """
    audio_files = {}
    
    for response in responses:
        if response.response_type == 'audio' and response.file_path:
            # Extract the relative path structure from file_path
            relative_path = None
            
            if response.file_path.startswith('http'):
                # S3 URL - extract the S3 key (question_id/filename)
                # URL format: https://bucket.s3.region.amazonaws.com/question_id/filename
                url_parts = response.file_path.replace('https://', '').replace('http://', '').split('/', 1)
                if len(url_parts) == 2:
                    # Remove query parameters if present
                    relative_path = url_parts[1].split('?')[0]
                else:
                    current_app.logger.warning(f"Invalid S3 URL format: {response.file_path}")
                    continue
            else:
                # Local path - already in format question_id/filename
                relative_path = response.file_path
            
            if not relative_path:
                continue
            
            # Create the full local path maintaining the folder structure
            local_path = os.path.join(temp_dir, relative_path)
            
            if response.file_path.startswith('http'):
                # Download from S3
                if download_audio_from_s3(response.file_path, local_path):
                    # Store relative path for zip creation
                    audio_files[response.id] = relative_path
                else:
                    current_app.logger.warning(f"Failed to download audio for response {response.id}")
            else:
                # Copy from local storage
                # # Construct full source path
                # upload_folder = current_app.config.get("UPLOAD_FOLDER", "_uploads")
                source_path = relative_path
                
                if os.path.exists(source_path):
                    try:
                        # Ensure destination directory exists
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                        shutil.copy2(source_path, local_path)
                        # Store relative path for zip creation
                        audio_files[response.id] = relative_path
                        current_app.logger.info(f"Copied local file {source_path} to {local_path}")
                    except Exception as e:
                        current_app.logger.error(f"Error copying file {source_path}: {e}")
                else:
                    current_app.logger.warning(f"Local file not found: {source_path}")
    
    return audio_files


def create_export_zip(responses: List, csv_path: str, audio_files: Dict[str, str], output_path: str, temp_dir: str):
    """
    Create a zip file containing CSV and audio files.
    Maintains the folder structure from file_path in the zip.
    
    Args:
        responses: List of Response objects
        csv_path: Path to CSV file
        audio_files: Dictionary mapping response_id to relative audio file path (from temp_dir)
        output_path: Path where zip file should be created
        temp_dir: Temporary directory where audio files are stored
    """
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Add CSV file
        zipf.write(csv_path, 'survey_responses.csv')
        
        # Add audio files maintaining folder structure
        for response_id, relative_path in audio_files.items():
            # Construct full path to the file in temp_dir
            full_audio_path = os.path.join(temp_dir, relative_path)
            
            if os.path.exists(full_audio_path):
                # Maintain folder structure in zip: audio/question_id/filename
                arcname = f"audio/{relative_path}"
                zipf.write(full_audio_path, arcname)
            else:
                current_app.logger.warning(f"Audio file not found: {full_audio_path}")
    
    current_app.logger.info(f"Created zip file {output_path} with {len(audio_files)} audio files")

