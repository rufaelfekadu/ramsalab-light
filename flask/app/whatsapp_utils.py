"""
WhatsApp Client for handling message sending and API interactions
"""
import os
import requests
import json
from typing import Dict, Any, Optional
from pathlib import Path
from app.models import Response, SurveyLogic, Survey
from flask import current_app


class WhatsAppClient:
    """Client for interacting with WhatsApp Business API"""
    
    def __init__(self, access_token: str = None, phone_number_id: str = None):
        """
        Initialize WhatsApp client
        
        Args:
            access_token: WhatsApp access token
            phone_number_id: WhatsApp phone number ID
        """
        self.access_token = access_token or os.getenv('WHATSAPP_ACCESS_TOKEN')
        self.phone_number_id = phone_number_id or os.getenv('WHATSAPP_FROM_PHONE_NUMBER_ID')
        self.base_url = os.getenv('WHATSAPP_URL', "https://graph.facebook.com/v22.0")
        
        if not self.access_token or not self.phone_number_id:
            raise ValueError("WhatsApp credentials not configured")
    
    def _make_request(self, endpoint: str, data: Dict[str, Any]) -> requests.Response:
        """
        Make a request to WhatsApp API
        
        Args:
            endpoint: API endpoint
            data: Request payload
            
        Returns:
            Response object
        """
        url = f"{self.base_url}/{self.phone_number_id}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        print(f"{url}")
        print(f"{headers}")
        print(f"{data}")
        return requests.post(url, headers=headers, json=data)
    
    def send_text_message(self, to: str, text: str, preview_url: bool = False) -> requests.Response:
        """
        Send a text message
        
        Args:
            to: Recipient phone number
            text: Message text
            preview_url: Whether to show URL preview
            
        Returns:
            Response object
        """
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {
                "preview_url": preview_url,
                "body": text
            }
        }
        
        return self._make_request("messages", data)
    
    def send_interactive_message(self, to: str, interactive_data: Dict[str, Any]) -> requests.Response:
        """
        Send an interactive message (buttons or lists)
        
        Args:
            to: Recipient phone number
            interactive_data: Interactive message configuration
            
        Returns:
            Response object
        """
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive_data
        }
        
        return self._make_request("messages", data)
    
    def send_button_message(self, to: str, body_text: str, buttons: list, 
                          header_text: str = "", footer_text: str = "") -> requests.Response:
        """
        Send a button interactive message
        
        Args:
            to: Recipient phone number
            body_text: Main message text
            buttons: List of button configurations
            header_text: Optional header text
            footer_text: Optional footer text
            
        Returns:
            Response object
        """
        interactive_data = {
            "action": {
                "buttons": buttons
            },
            "body": {
                "text": body_text
            },
            "type": "button"
        }
        
        if header_text:
            interactive_data["header"] = {
                "type": "text",
                "text": header_text
            }
        
        if footer_text:
            interactive_data["footer"] = {
                "text": footer_text
            }
        
        return self.send_interactive_message(to, interactive_data)
    
    def send_list_message(self, to: str, body_text: str, list_button: str, sections: list,
                         header_text: str = "", footer_text: str = "") -> requests.Response:
        """
        Send a list interactive message
        
        Args:
            to: Recipient phone number
            body_text: Main message text
            list_button: Button text for the list
            sections: List sections configuration
            header_text: Optional header text
            footer_text: Optional footer text
            
        Returns:
            Response object
        """
        interactive_data = {
            "action": {
                "button": list_button,
                "sections": sections
            },
            "body": {
                "text": body_text
            },
            "type": "list"
        }
        
        if header_text:
            interactive_data["header"] = {
                "type": "text",
                "text": header_text
            }
        
        if footer_text:
            interactive_data["footer"] = {
                "text": footer_text
            }
        
        return self.send_interactive_message(to, interactive_data)
    
    def send_question_message(self, to: str, question_data: Dict[str, Any]) -> requests.Response:
        """
        Send a question message based on question configuration
        
        Args:
            to: Recipient phone number
            question_data: Question configuration from database
            
        Returns:
            Response object
        """
        question_type = question_data.get("question_type")
        question_text = question_data.get("text")
        question_options = question_data.get("options", [])
        
        if len(question_options) > 0: question_options = question_options[0]

        if question_type == "text" or question_type == "audio":
            return self.send_text_message(to, question_text)
        
        elif question_type == "interactive":
            interactive_type = question_options.get("interactive_type")
            header_text = question_options.get("header_text", "")
            body_text = question_options.get("body_text", question_text)
            footer_text = question_options.get("footer_text", "")
            
            if interactive_type == "button":
                buttons = question_options.get("buttons", [])
                
                buttons = [{"type": "reply", "reply":{"id": button['id'], "title": button['title']}} for button in buttons]
                return self.send_button_message(to, body_text, buttons, header_text, footer_text)
            
            elif interactive_type == "list":
                list_button = question_options.get("button", "Select an option")
                sections = question_options.get("sections", [])
                sections = [{"title": section["title"], "rows": [{"id": row["id"], "title": row["title"]} for row in section["rows"]]} for section in sections]
                return self.send_list_message(to, body_text, list_button, sections, header_text, footer_text)
            
            else:
                raise ValueError(f"Unsupported interactive type: {interactive_type}")
        
        else:
            raise ValueError(f"Unsupported question type: {question_type}")

    def is_message_sent_successfully(self, response: requests.Response) -> bool:
        """
        Check if message was sent successfully
        """
        print(f"response: {response}")
        return response.status_code == 200

class WhatsAppMediaHandler:
    """Handler for downloading and processing WhatsApp media files"""
    
    def __init__(self, access_token: str = None, downloads_directory: str = None):
        """
        Initialize media handler
        
        Args:
            access_token: WhatsApp access token
            downloads_directory: Directory to save downloaded media
        """
        self.access_token = access_token or os.getenv('WHATSAPP_ACCESS_TOKEN')
        self.downloads_directory = downloads_directory or os.getenv('DOWNLOADS_DIRECTORY', '_uploads')
        self.base_url = os.getenv('WHATSAPP_URL', "https://graph.facebook.com/v22.0")
        
        # Ensure downloads directory exists
        Path(self.downloads_directory).mkdir(parents=True, exist_ok=True)
        
        if not self.access_token:
            raise ValueError("WhatsApp access token not configured")
    
    def _get_media_url(self, media_id: str) -> str:
        """
        Get media URL from WhatsApp API
        
        Args:
            media_id: Media ID from WhatsApp
            
        Returns:
            Media download URL
            
        Raises:
            ValueError: If unable to get media URL
        """
        response = requests.get(
            f"{self.base_url}/{media_id}/",
            headers={"Authorization": f"Bearer {self.access_token}"}
        )
        
        if response.status_code == 200:
            return json.loads(response.text)["url"]
        else:
            raise ValueError(f"Failed to get media URL: {response.status_code}")
    
    def _download_media(self, url: str, file_path: str) -> None:
        """
        Download media file from URL
        
        Args:
            url: Media download URL
            file_path: Local file path to save media
            
        Raises:
            ValueError: If download fails
        """
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.access_token}"}
        )
        
        if response.status_code == 200:
            with open(file_path, 'wb') as f:
                f.write(response.content)
        else:
            raise ValueError(f"Failed to download media: {response.status_code}")
    
    def _get_file_extension(self, mime_type: str, media_type: str) -> str:
        """
        Get file extension based on MIME type and media type
        
        Args:
            mime_type: MIME type from WhatsApp
            media_type: Type of media (video, audio, etc.)
            
        Returns:
            File extension with dot
            
        Raises:
            ValueError: If MIME type is not supported
        """
        mime_to_extension = {
            "video/mp4": ".mp4",
            "image/webp": ".webp",
            "audio/ogg; codecs=opus": ".ogg",
            "image/jpeg": ".jpeg",
            "application/pdf": ".pdf"
        }
        
        if mime_type not in mime_to_extension:
            raise ValueError(f"Unsupported MIME type: {mime_type}")
        
        return mime_to_extension[mime_type]
    
    def process_video(self, message_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process video message
        
        Args:
            message_metadata: Video message metadata
            
        Returns:
            Processed media metadata
        """
        video_data = message_metadata["video"]
        video_id = video_data["id"]
        mime_type = video_data["mime_type"]
        caption = video_data.get("caption")
        
        # Get file extension and download URL
        extension = self._get_file_extension(mime_type, "video")
        media_url = self._get_media_url(video_id)
        
        # Download video
        file_path = os.path.join(self.downloads_directory, f"{video_id}{extension}")
        self._download_media(media_url, file_path)
        
        return {
            "message_type": "video",
            "media_download_location": file_path,
            "video_download_location": file_path,
            "video_caption_field": caption
        }
    
    def process_sticker(self, message_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process sticker message
        
        Args:
            message_metadata: Sticker message metadata
            
        Returns:
            Processed media metadata
        """
        sticker_data = message_metadata["sticker"]
        sticker_id = sticker_data["id"]
        mime_type = sticker_data["mime_type"]
        animated = sticker_data["animated"]
        
        # Get file extension and download URL
        extension = self._get_file_extension(mime_type, "sticker")
        media_url = self._get_media_url(sticker_id)
        
        # Download sticker
        file_path = os.path.join(self.downloads_directory, f"{sticker_id}{extension}")
        self._download_media(media_url, file_path)
        
        return {
            "message_type": "sticker",
            "media_download_location": file_path,
            "sticker_download_location": file_path,
            "sticker_animated_yn_field": animated
        }
    
    def process_audio(self, message_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process audio message
        
        Args:
            message_metadata: Audio message metadata
            
        Returns:
            Processed media metadata
        """
        audio_data = message_metadata["audio"]
        audio_id = audio_data["id"]
        mime_type = audio_data["mime_type"]
        voice = audio_data["voice"]
        
        # Get file extension and download URL
        extension = self._get_file_extension(mime_type, "audio")
        media_url = self._get_media_url(audio_id)
        
        # Download audio
        file_path = os.path.join(self.downloads_directory, f"{audio_id}{extension}")
        self._download_media(media_url, file_path)
        
        return {
            "message_type": "audio",
            "media_download_location": file_path,
            "audio_download_location": file_path,
            "audio_voice_yn_field": voice
        }
    
    def process_image(self, message_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process image message
        
        Args:
            message_metadata: Image message metadata
            
        Returns:
            Processed media metadata
        """
        image_data = message_metadata["image"]
        image_id = image_data["id"]
        mime_type = image_data["mime_type"]
        caption = image_data.get("caption")
        
        # Get file extension and download URL
        extension = self._get_file_extension(mime_type, "image")
        media_url = self._get_media_url(image_id)
        
        # Download image
        file_path = os.path.join(self.downloads_directory, f"{image_id}{extension}")
        self._download_media(media_url, file_path)
        
        return {
            "message_type": "image",
            "media_download_location": file_path,
            "image_download_location": file_path,
            "image_caption_field": caption
        }
    
    def process_document(self, message_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process document message
        
        Args:
            message_metadata: Document message metadata
            
        Returns:
            Processed media metadata
        """
        document_data = message_metadata["document"]
        document_id = document_data["id"]
        mime_type = document_data["mime_type"]
        caption = document_data.get("caption")
        
        # Get file extension and download URL
        extension = self._get_file_extension(mime_type, "document")
        media_url = self._get_media_url(document_id)
        
        # Download document
        file_path = os.path.join(self.downloads_directory, f"{document_id}{extension}")
        self._download_media(media_url, file_path)
        
        return {
            "message_type": "document",
            "media_download_location": file_path,
            "document_download_location": file_path,
            "document_caption_field": caption
        }
    
    def process_media(self, message_metadata: list) -> Dict[str, Any]:
        """
        Process media message based on type
        
        Args:
            message_metadata: Message metadata from WhatsApp webhook
            
        Returns:
            Processed media metadata
        """
        if not message_metadata:
            raise ValueError("Empty message metadata")
        
        message_data = message_metadata[0]
        message_type = message_data["type"]
        
        # Route to appropriate processor
        processors = {
            "video": self.process_video,
            "sticker": self.process_sticker,
            "audio": self.process_audio,
            "image": self.process_image,
            "document": self.process_document
        }
        
        if message_type not in processors:
            raise ValueError(f"Unsupported media type: {message_type}")
        
        return processors[message_type](message_data)

def _parse_whatsapp_message(message_metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse a WhatsApp message
    
    Args:
        message_metadata: WhatsApp message metadata
        media_handler: WhatsApp media handler
        
    Returns:
        Parsed message dictionary
    """

    from_field = message_metadata[0]["from"]
    message_type = message_metadata[0]["type"]

    # Handle media messages
    media_download_location = 'none'
    message_media_metadata = {}


    # Extract text and interactive responses
    text_field = None
    interactive_field_type = None
    interactive_field_reply_button_id = None
    interactive_field_reply_button_title = None
    interactive_field_list_id = None
    interactive_field_list_title = None
    interactive_field_list_description = None
    
    if message_type == "text":
        text_field = message_metadata[0]["text"]["body"]
    elif message_type == "interactive":
        interactive_field_type = message_metadata[0]["interactive"]["type"]
        if interactive_field_type == "button_reply":
            interactive_field_reply_button_id = message_metadata[0]["interactive"]["button_reply"]["id"]
            interactive_field_reply_button_title = message_metadata[0]["interactive"]["button_reply"]["title"]
        elif interactive_field_type == "list_reply":
            interactive_field_list_id = message_metadata[0]["interactive"]["list_reply"]["id"]
            interactive_field_list_title = message_metadata[0]["interactive"]["list_reply"]["title"]
            # interactive_field_list_description = message_metadata[0]["interactive"]["list_reply"]["description"]

    return {
        "from_field": from_field,
        "message_type": message_type,
        "text_field": text_field,
        "media_download_location": media_download_location,
        "message_media_metadata": message_media_metadata,
        "interactive_field_type": interactive_field_type,
        "interactive_field_reply_button_id": interactive_field_reply_button_id,
        "interactive_field_reply_button_title": interactive_field_reply_button_title,
        "interactive_field_list_id": interactive_field_list_id,
        "interactive_field_list_title": interactive_field_list_title,
        "interactive_field_list_description": interactive_field_list_description
    }

def _create_whatsapp_response_from_message(user, current_question, parsed_message):
    """
    Create a Response object based on message type and content
    
    Returns:
        Response object or None if message type is not supported
    """
    # Use last_question_asked if available, otherwise fall back to current_question.id
    question_id = user.last_question_asked if user.last_question_asked else current_question.id

    metadata_json = {}
    message_type = parsed_message["message_type"]
    media_download_location = parsed_message["media_download_location"]
    interactive_field_reply_button_title = parsed_message["interactive_field_reply_button_title"]
    interactive_field_list_title = parsed_message["interactive_field_list_title"]
    
    if message_type == "text":
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type="text",
            response_value=parsed_message["text_field"],
            file_path=None,
            response_metadata=None
        )

    elif message_type == "video":
        video_caption = parsed_message["message_media_metadata"]["video_caption_field"]
        metadata_json = {"caption": video_caption} if video_caption else None
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type="video",
            response_value=video_caption,
            file_path=parsed_message["media_download_location"],
            response_metadata=metadata_json
        )

    elif message_type == "sticker":
        metadata_json = {"animated": parsed_message["message_media_metadata"].get("sticker_animated_yn_field")}
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type=message_type,
            response_value=None,
            file_path=media_download_location,
            response_metadata=metadata_json
        )

    elif message_type == "audio":
        metadata_json = {"voice": parsed_message["message_media_metadata"].get("audio_voice_yn_field")}
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type=message_type,
            response_value=None,
            file_path=media_download_location,
            response_metadata=metadata_json
        )

    elif message_type == "image":
        image_caption = parsed_message["message_media_metadata"].get("image_caption_field")
        metadata_json = {"caption": image_caption} if image_caption else None
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type=message_type,
            response_value=image_caption,
            file_path=media_download_location,
            response_metadata=metadata_json
        )

    elif message_type == "document":
        document_caption = parsed_message["message_media_metadata"].get("document_caption_field")
        metadata_json = {"caption": document_caption} if document_caption else None
        return Response(
            user_id=user.id,
            question_id=question_id,
            response_type=message_type,
            response_value=document_caption,
            file_path=media_download_location,
            response_metadata=metadata_json
        )

    elif message_type == "interactive":
        if parsed_message["interactive_field_type"] == "button_reply":
            metadata_json = {
                "interactive_type": parsed_message["interactive_field_type"],
                "button_id": parsed_message["interactive_field_reply_button_id"],
                "button_title": parsed_message["interactive_field_reply_button_title"]
            }
            return Response(
                user_id=user.id,
                question_id=question_id,
                response_type=message_type,
                response_value=interactive_field_reply_button_title,
                file_path=None,
                response_metadata=metadata_json
            )
        elif parsed_message["interactive_field_type"] == "list_reply":
            metadata_json = {
                "interactive_type": parsed_message["interactive_field_type"],
                "list_id": parsed_message["interactive_field_list_id"],
                "list_title": parsed_message["interactive_field_list_title"],
                "list_description": parsed_message["interactive_field_list_description"]
            }
            return Response(
                user_id=user.id,
                question_id=question_id,
                response_type=message_type,
                response_value=interactive_field_list_title,
                file_path=None,
                response_metadata=metadata_json
            )
    
    return None

def _handle_whatsapp_survey_logic(survey_name, current_question, parsed_message):
    """
    Handle survey logic for interactive responses
    
    Returns:
        Next prompt number (may be modified by logic)
    """
    interactive_field_type = parsed_message["interactive_field_type"]
    interactive_field_reply_button_id = parsed_message["interactive_field_reply_button_id"]
    interactive_field_list_id = parsed_message["interactive_field_list_id"]

    try:
        # Look up survey by name to get survey_id
        survey = Survey.query.filter_by(name=survey_name).first()
        if not survey:
            current_app.logger.warning(f'Survey {survey_name} not found for logic check')
            return current_question.prompt_number + 1

        if interactive_field_type == "button_reply":
            current_app.logger.debug(f'Checking logic for survey {survey_name}, question {current_question.id}, button {interactive_field_reply_button_id}')
            logic = SurveyLogic.query.filter_by(
                survey_id=survey.id,
                question_id=current_question.id,
                response_option_id=interactive_field_reply_button_id
            ).first()
            
            if logic and logic.next_question:
                next_prompt = logic.next_question.prompt_number
                current_app.logger.info(f'Found logic: jumping to prompt {next_prompt}')
                return next_prompt
                
        elif interactive_field_type == "list_reply":
            current_app.logger.debug(f'Checking logic for survey {survey_name}, question {current_question.id}, list {interactive_field_list_id}')
            logic = SurveyLogic.query.filter_by(
                survey_id=survey.id,
                question_id=current_question.id,
                response_option_id=interactive_field_list_id
            ).first()
            
            if logic and logic.next_question:
                next_prompt = logic.next_question.prompt_number
                current_app.logger.info(f'Found logic: jumping to prompt {next_prompt}')
                return next_prompt

    except Exception as e:
        current_app.logger.error(f'Error checking logic: {e}', exc_info=True)
    
    return current_question.prompt_number + 1
