"""
WhatsApp webhook handlers - extracted from routes.py for better organization.
"""
import os
import re
from flask import current_app

from app.database import db
from app.models import User, Question, Survey
from app.whatsapp_utils import (
    WhatsAppClient,
    WhatsAppMediaHandler,
    _parse_whatsapp_message,
    _create_whatsapp_response_from_message
)
# Note: create_new_user_token is defined in routes.py, but we'll define it here to avoid circular import
def _create_new_user_token():
    """Create a new unique user token"""
    import uuid
    token = str(uuid.uuid4())
    while User.query.filter_by(token=token).first():
        token = str(uuid.uuid4())
    return token


def handle_whatsapp_verification(mode, token, challenge):
    """
    Handle WhatsApp webhook verification (GET request).
    
    Args:
        mode: hub.mode parameter
        token: hub.verify_token parameter
        challenge: hub.challenge parameter
    
    Returns:
        tuple: (response, status_code) - challenge value if valid, error otherwise
    """
    if mode == "subscribe" and token == os.getenv('WHATSAPP_VERIFY_TOKEN'):
        return challenge, 200
    else:
        return "Invalid token", 403


def handle_whatsapp_webhook(request_data, request_method, request_args):
    """
    Main entry point for WhatsApp webhook handling.
    
    Args:
        request_data: JSON data from request
        request_method: HTTP method (GET or POST)
        request_args: Request args (for GET verification)
    
    Returns:
        tuple: (response, status_code)
    """
    # Handle webhook verification (GET request)
    if request_method == "GET":
        mode = request_args.get("hub.mode")
        challenge_value = request_args.get("hub.challenge")
        token = request_args.get("hub.verify_token")
        return handle_whatsapp_verification(mode, token, challenge_value)
    
    # Handle incoming messages (POST request)
    if request_method == "POST":
        current_app.logger.info(f"Received WhatsApp webhook request")
        
        # Handle status notifications (sent, delivered, read acknowledgements)
        try:
            message_metadata = request_data["entry"][0]["changes"][0]["value"]["statuses"]
            current_app.logger.info("Ignored status notification")
            return "OK", 200
        except KeyError:
            pass
        
        # Parse message
        current_app.logger.info('Processing WhatsApp message')
        message_metadata = request_data["entry"][0]["changes"][0]["value"]["messages"]
        current_app.logger.info(f'Message metadata: {message_metadata}')
        parsed_message = _parse_whatsapp_message(message_metadata)
        
        # Get or create user
        user = User.query.filter_by(phone_number=parsed_message["from_field"]).first()
        if not user:
            current_app.logger.info(f'Creating new WhatsApp user for phone: {parsed_message["from_field"]}')
            user = User(
                phone_number=parsed_message["from_field"],
                token=_create_new_user_token(),
                survey_name=os.getenv('WHATSAPP_DEFAULT_SURVEY', 'example_survey'),
                last_prompt_sent=None
            )
            db.session.add(user)
            db.session.commit()
        
        # Assign survey to user
        survey_name = user.survey_name or os.getenv('WHATSAPP_DEFAULT_SURVEY', 'example_survey')
        survey = Survey.query.filter_by(name=survey_name).first()
        if not survey:
            current_app.logger.error(f'Survey {survey_name} not found')
            return "Survey not found", 404
        
        # Route to appropriate handler
        if not user.demographics_and_consent_completed:
            return handle_demographic_consent_flow(parsed_message, user, message_metadata)
        else:
            return handle_survey_flow(parsed_message, user, survey, message_metadata)


def handle_demographic_consent_flow(parsed_message, user, message_metadata):
    """
    Handle demographic and consent questionnaire flow.
    
    Args:
        parsed_message: Parsed WhatsApp message
        user: User object
        message_metadata: Raw message metadata
    
    Returns:
        tuple: (response, status_code)
    """
    whatsapp_client = WhatsAppClient()
    
    # Step 1: Consent read form
    if not user.consent_read_form:
        if parsed_message.get("message_type") != "interactive":
            # Send first message of demographic/consent workflow
            consent_message = "Please visit kaizoderp.com/participant-information to view our terms and conditions. Do you accept the terms and conditions?"
            buttons = [
                {"type": "reply", "reply": {"id": "consent_yes", "title": "Yes"}},
                {"type": "reply", "reply": {"id": "consent_no", "title": "No"}}
            ]
            message_response = whatsapp_client.send_button_message(
                parsed_message["from_field"],
                consent_message,
                buttons
            )
            if whatsapp_client.is_message_sent_successfully(message_response):
                current_app.logger.info(f'Sent consent question to {parsed_message["from_field"]}')
                return "OK", 200
            else:
                current_app.logger.error(f"Failed to send consent question: {message_response.status_code} - {message_response.text}")
                return "Failed to send consent question", 500
        
        elif parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "button_reply":
            button_id = parsed_message.get("interactive_field_reply_button_id")
            if button_id == "consent_yes":
                user.consent_read_form = True
                db.session.commit()
                current_app.logger.info(f'User {parsed_message["from_field"]} accepted terms and conditions')
                return _send_citizenship_question(parsed_message["from_field"], whatsapp_client)
            elif button_id == "consent_no":
                decline_message = "Thank you for your interest. Unfortunately, we cannot proceed without your acceptance of the terms and conditions."
                message_response = whatsapp_client.send_text_message(parsed_message["from_field"], decline_message)
                if whatsapp_client.is_message_sent_successfully(message_response):
                    current_app.logger.info(f'User {parsed_message["from_field"]} declined terms and conditions')
                    return "OK", 200
                else:
                    current_app.logger.error(f"Failed to send decline message: {message_response.status_code} - {message_response.text}")
                    return "Failed to send message", 500
            else:
                raise ValueError("The only expected values are \"consent_yes\" and \"consent_no\".")
        else:
            raise ValueError("There shouldn't be a non-button-reply interactive message from the user when user.consent_read_form=False.")
    
    # Step 2: Emirati citizenship
    elif user.consent_read_form and user.emirati_citizenship is None:
        if parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "button_reply":
            button_id = parsed_message.get("interactive_field_reply_button_id")
            if button_id in ["citizenship_yes", "citizenship_no"]:
                user.emirati_citizenship = (button_id == "citizenship_yes")
                db.session.commit()
                current_app.logger.info(f'User {parsed_message["from_field"]} answered citizenship: {user.emirati_citizenship}')
                return _send_age_group_question(parsed_message["from_field"], whatsapp_client)
            else:
                raise ValueError("The only expected values in this context are \"citizenship_yes\" and \"citizenship_no\".")
        else:
            raise ValueError("The only expected type of response here is an \"interactive\" \"button_reply\".")
    
    # Step 3: Age group
    elif user.emirati_citizenship is not None and user.age_group is None:
        if parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "list_reply":
            list_id = parsed_message.get("interactive_field_list_id")
            if list_id and list_id.startswith("age_"):
                age_value = int(list_id.split("_")[1])
                user.age_group = age_value
                db.session.commit()
                current_app.logger.info(f'User {parsed_message["from_field"]} answered age group: {age_value}')
                return _send_place_of_birth_question(parsed_message["from_field"], whatsapp_client)
            else:
                raise ValueError("All valid responses will have values of the form \"age_*\".")
        else:
            raise ValueError(f"The only expected type of response here is an \"interactive\" \"list_reply\". Instead got: {parsed_message}")
    
    # Step 4: Place of birth
    elif user.age_group is not None and user.place_of_birth is None:
        return _handle_place_of_birth_response(parsed_message, user, whatsapp_client)
    
    # Step 5: Current residence
    elif user.place_of_birth is not None and user.current_residence is None:
        return _handle_current_residence_response(parsed_message, user, whatsapp_client)
    
    # Step 6: Optional name and contact
    elif user.current_residence is not None and user.real_name_optional_input is None and user.phone_number_optional_input is None:
        if parsed_message.get("message_type") == "text":
            name_and_number_values = parsed_message.get("text_field")
            try:
                name_value = name_and_number_values.split("\n")[0]
                number_value = name_and_number_values.split("\n")[1]
            except IndexError:
                if re.search(r'[A-Za-z]', name_and_number_values):
                    name_value = name_and_number_values
                    number_value = None
                else:
                    name_value = None
                    number_value = name_and_number_values
            except AttributeError:
                raise AttributeError(
                    f"Something went wrong with name_and_number_values: {name_and_number_values}.\n" +
                    f"parsed_message: {parsed_message}"
                )
            user.real_name_optional_input = name_value
            user.phone_number_optional_input = number_value
            db.session.commit()
            current_app.logger.info(f'User {parsed_message["from_field"]} answered with optional information')
            return _send_consent_question_1(parsed_message["from_field"], whatsapp_client)
        else:
            raise ValueError("The only expected type of response here is \"text\".")
    
    # Step 7: Consent question 1
    elif (user.real_name_optional_input is not None or user.phone_number_optional_input is not None) and user.consent_required is None:
        if parsed_message.get("interactive_field_type") == "button_reply":
            button_id = parsed_message.get("interactive_field_reply_button_id")
            if button_id in ["consent_required_yes", "consent_required_no"]:
                user.consent_required = (button_id == "consent_required_yes")
                db.session.commit()
                current_app.logger.info(f'User {parsed_message["from_field"]} answered consent question 1: {user.consent_required}')
                return _send_consent_question_2(parsed_message["from_field"], whatsapp_client)
            else:
                raise ValueError("All valid responses will have values of \"consent_required_yes\" or \"consent_required_no\".")
        else:
            raise ValueError("The only expected type of response here is an \"interactive\" \"button reply\".")
    
    # Step 8: Consent question 2
    elif user.consent_required is not None and user.consent_optional is None:
        if parsed_message.get("interactive_field_type") == "button_reply":
            button_id = parsed_message.get("interactive_field_reply_button_id")
            if button_id in ["consent_optional_yes", "consent_optional_no"]:
                if button_id == "consent_optional_yes":
                    user.demographics_and_consent_completed = True
                    user.consent_optional = True
                else:
                    user.consent_optional = False
                db.session.commit()
                current_app.logger.info(f'User {parsed_message["from_field"]} answered consent question 2: {button_id == "consent_optional_yes"}')
                
                if button_id == "consent_optional_yes":
                    current_app.logger.info(f'User {parsed_message["from_field"]} finished the onboarding process!')
                    return _send_onboarding_completion(parsed_message["from_field"], whatsapp_client)
                elif button_id == "consent_optional_no":
                    return _send_consent_question_3(parsed_message["from_field"], whatsapp_client)
            else:
                raise ValueError("All valid responses will have values of \"consent_optional_yes\" or \"consent_optional_no\".")
        else:
            raise ValueError("The only expected type of response here is an \"interactive\" \"button reply\".")
    
    # Step 9: Consent question 3
    elif user.consent_optional is not None and user.consent_optional_alternative is None:
        if parsed_message.get("interactive_field_type") == "button_reply":
            button_id = parsed_message.get("interactive_field_reply_button_id")
            if button_id in ["consent_optional_alt_yes", "consent_optional_alt_no"]:
                user.consent_optional_alternative = (button_id == "consent_optional_alt_yes")
                user.demographics_and_consent_completed = True
                db.session.commit()
                current_app.logger.info(f'User {parsed_message["from_field"]} answered consent question 3: {user.consent_optional_alternative}')
                current_app.logger.info(f'User {parsed_message["from_field"]} finished the onboarding process!')
                return _send_onboarding_completion(parsed_message["from_field"], whatsapp_client)
            else:
                raise ValueError(
                    f"All valid responses will have values of \"consent_optional_alt_yes\" or \"consent_optional_alt_no\".\n" +
                    f"Instead, we have button_id = {button_id}."
                )
        else:
            raise ValueError("The only expected type of response here is an \"interactive\" \"button reply\".")
    
    else:
        raise ValueError("Check this user's database state. Something's wrong.")


def handle_survey_flow(parsed_message, user, survey, message_metadata):
    """
    Handle survey question flow after demographics/consent is completed.
    
    Args:
        parsed_message: Parsed WhatsApp message
        user: User object
        survey: Survey object
        message_metadata: Raw message metadata
    
    Returns:
        tuple: (response, status_code)
    """
    if user.last_prompt_sent is None:
        new_prompt_number = 0
    else:
        new_prompt_number = user.last_prompt_sent + 1
        
        # Record response from user
        current_question = Question.query.filter_by(
            survey_id=survey.id,
            prompt_number=user.last_prompt_sent
        ).first()
        if not current_question:
            current_app.logger.warning(f'No question found for prompt {user.last_prompt_sent} in survey {survey.name}')
            return "No current question found", 400
        
        # Check if we're handling a question from a "select" group
        current_question_group = current_question.question_group if current_question.question_group_id else None
        question_is_from_select_group = current_question_group and current_question_group.group_type == "select"
        response_is_list_selection = parsed_message.get("interactive_field_type") == "list_reply" and question_is_from_select_group
        
        # If the response is the user's question selection from the list
        if question_is_from_select_group and response_is_list_selection:
            selected_question_id = parsed_message.get("interactive_field_list_id")
            try:
                selected_question_id = int(selected_question_id)
            except (ValueError, TypeError):
                current_app.logger.error(f'Invalid question ID in list selection: {selected_question_id}')
                return "Invalid selection", 400
            
            selected_question = Question.query.filter_by(id=selected_question_id).first()
            if not selected_question or selected_question.question_group_id != current_question_group.id:
                current_app.logger.error(f'Selected question {selected_question_id} not found or not in group')
                return "Invalid question selection", 400
            
            # Use the selected question as the current question
            current_question = selected_question
            current_app.logger.info(f'User selected question {selected_question_id} from select group')
            
            # Send the selected question
            question_data = {
                "question_type": current_question.question_type,
                "text": current_question.prompt,
                "options": current_question.options or {}
            }
            current_app.logger.info(f'Sending selected question data: {question_data}')
            whatsapp_client = WhatsAppClient()
            message_response = whatsapp_client.send_question_message(parsed_message["from_field"], question_data)
            if whatsapp_client.is_message_sent_successfully(message_response):
                user.last_question_asked = current_question.id
                db.session.commit()
                current_app.logger.info(f'Sent selected question {current_question.id} to {parsed_message["from_field"]}')
                return "OK", 200
            else:
                current_app.logger.error(f"Failed to send selected question: {message_response.status_code} - {message_response.text}")
                return "Failed to send question", 500
        
        # Process media messages
        else:
            if parsed_message['message_type'] in ["document", "sticker", "audio", "image", "video"]:
                from app.utils import _is_spaces_enabled, _generate_safe_filename
                
                media_handler = WhatsAppMediaHandler(downloads_directory=os.path.join(current_app.config["UPLOAD_FOLDER"], str(current_question.id)))
                
                # Try streaming directly to Spaces if enabled (avoids local disk I/O)
                if _is_spaces_enabled():
                    try:
                        # Extract media info to generate Spaces key
                        message_data = message_metadata[0]
                        media_type = message_data["type"]
                        media_id = message_data[media_type]["id"]
                        mime_type = message_data[media_type]["mime_type"]
                        
                        # Get file extension
                        extension = media_handler._get_file_extension(mime_type, media_type)
                        safe_name = _generate_safe_filename(str(user.id), None, extension)
                        spaces_key = f"{current_question.id}/{safe_name}"
                        
                        # Get media URL and stream directly to Spaces
                        media_url = media_handler._get_media_url(media_id)
                        spaces_key = media_handler._stream_media_to_spaces(
                            media_url, spaces_key, str(current_question.id), str(user.id)
                        )
                        
                        # Set media location to Spaces key
                        parsed_message['media_download_location'] = spaces_key
                        
                        # Process metadata (without local file)
                        message_media_metadata = media_handler._get_media_metadata(message_data, spaces_key)
                        parsed_message['message_media_metadata'] = message_media_metadata
                        
                        current_app.logger.info(f"Streamed WhatsApp {media_type} directly to Spaces: {spaces_key}")
                    except Exception as e:
                        current_app.logger.error(f"Failed to stream WhatsApp media to Spaces, falling back to local: {e}")
                        # Fall back to local download
                        message_media_metadata = media_handler.process_media(message_metadata)
                        local_file_path = message_media_metadata.get("media_download_location", "none")
                        parsed_message['media_download_location'] = local_file_path
                        parsed_message['message_media_metadata'] = message_media_metadata
                else:
                    # Spaces not enabled, use local storage
                    message_media_metadata = media_handler.process_media(message_metadata)
                    local_file_path = message_media_metadata.get("media_download_location", "none")
                    parsed_message['media_download_location'] = local_file_path
                    parsed_message['message_media_metadata'] = message_media_metadata
            
            # Check for audio requirement
            if current_question.question_type == "audio" and parsed_message["media_download_location"] == "none":
                current_app.logger.info(f'No audio file provided for question {current_question.id}')
                return "No audio file provided", 400
            
            # Create response based on message type
            response = _create_whatsapp_response_from_message(
                user, current_question, parsed_message
            )
            if response:
                db.session.add(response)
                db.session.commit()
                current_app.logger.info(f'committed Response: {response}')
    
    # Handle sending out the next question
    return send_next_survey_question(user, survey, new_prompt_number, parsed_message["from_field"])


def send_next_survey_question(user, survey, prompt_number, phone_number):
    """
    Send the next survey question based on prompt number and group type.
    
    Args:
        user: User object
        survey: Survey object
        prompt_number: Next prompt number to send
        phone_number: User's phone number
    
    Returns:
        tuple: (response, status_code)
    """
    from sqlalchemy.sql.expression import func
    
    # Get the next question, if it exists
    next_question = Question.query.filter_by(
        survey_id=survey.id,
        prompt_number=prompt_number
    ).first()
    
    # If the previous question was the last question, send completion message
    if not next_question:
        current_app.logger.info(f'Survey completed for user {phone_number}. No more questions.')
        user.last_prompt_sent = prompt_number
        db.session.commit()
        return _send_survey_completion(user, phone_number)
    
    # Check if next_question belongs to a "select" or "random" group
    next_question_group = next_question.question_group if next_question.question_group_id else None
    next_question_is_from_select_group = next_question_group and next_question_group.group_type == "select"
    next_question_is_from_random_group = next_question_group and next_question_group.group_type == "random"
    
    whatsapp_client = WhatsAppClient()
    
    if next_question_is_from_select_group:
        # Get all questions in the group
        group_questions = Question.query.filter_by(
            question_group_id=next_question_group.id
        ).all()
        
        if not group_questions:
            current_app.logger.error(f'No questions found in select group {next_question_group.id}')
            return "Next question is from select group, but the group contains no questions.", 404
        
        # Create a list message with all questions
        list_items = []
        for q in group_questions:
            title = q.prompt[:24] if len(q.prompt) > 24 else q.prompt
            description = q.prompt[24:72] if len(q.prompt) > 24 else ""
            list_items.append({
                "id": str(q.id),
                "title": title,
                "description": description
            })
        sections = [{
            "title": "Select a question",
            "rows": list_items
        }]
        body_text = f"Please select which question you would like to answer:"
        message_response = whatsapp_client.send_list_message(
            phone_number,
            body_text,
            "Select Question",
            sections
        )
        if whatsapp_client.is_message_sent_successfully(message_response):
            user.last_prompt_sent = prompt_number
            user.last_question_asked = None
            db.session.commit()
            current_app.logger.info(f'Sent question selection list to {phone_number} for prompt {prompt_number}')
            return "OK", 200
        else:
            current_app.logger.error(f"Failed to send selection list: {message_response.status_code} - {message_response.text}")
            return "Failed to send selection list", 500
    
    elif next_question_is_from_random_group:
        random_next_question = Question.query.filter_by(
            survey_id=survey.id,
            prompt_number=prompt_number
        ).order_by(func.random()).first()
        question_data = {
            "question_type": random_next_question.question_type,
            "text": random_next_question.prompt,
            "options": random_next_question.options or {}
        }
        current_app.logger.info(f'Sending question data: {question_data}')
        message_response = whatsapp_client.send_question_message(phone_number, question_data)
        if whatsapp_client.is_message_sent_successfully(message_response):
            user.last_prompt_sent = prompt_number
            user.last_question_asked = random_next_question.id
            db.session.commit()
            current_app.logger.info(f'Updated user last_prompt_sent to {prompt_number}')
            return "OK", 200
        else:
            current_app.logger.error(f"Failed to send message to {phone_number}: {message_response.status_code} - {message_response.text}")
            return "Failed to send question message", 500
    
    else:
        # Sequential group or no group
        question_data = {
            "question_type": next_question.question_type,
            "text": next_question.prompt,
            "options": next_question.options or {}
        }
        current_app.logger.info(f'Sending question data: {question_data}')
        message_response = whatsapp_client.send_question_message(phone_number, question_data)
        if whatsapp_client.is_message_sent_successfully(message_response):
            user.last_prompt_sent = prompt_number
            user.last_question_asked = next_question.id
            db.session.commit()
            current_app.logger.info(f'Updated user last_prompt_sent to {prompt_number}')
            return "OK", 200
        else:
            current_app.logger.error(f"Failed to send message to {phone_number}: {message_response.status_code} - {message_response.text}")
            return "Failed to send question message", 500


# Helper functions for sending specific questions

def _send_citizenship_question(phone_number, whatsapp_client):
    """Send citizenship question."""
    citizenship_question = "Are you an Emirati citizen?"
    buttons = [
        {"type": "reply", "reply": {"id": "citizenship_yes", "title": "Yes"}},
        {"type": "reply", "reply": {"id": "citizenship_no", "title": "No"}}
    ]
    message_response = whatsapp_client.send_button_message(phone_number, citizenship_question, buttons)
    if whatsapp_client.is_message_sent_successfully(message_response):
        current_app.logger.info(f'Sent citizenship question to {phone_number}')
        return "OK", 200
    else:
        current_app.logger.error(f"Failed to send citizenship question: {message_response.status_code} - {message_response.text}")
        return "Failed to send question", 500


def _send_age_group_question(phone_number, whatsapp_client):
    """Send age group question."""
    age_question = "What is your age group?"
    list_items = [
        {"id": "age_1", "title": "18 to 25 years"},
        {"id": "age_2", "title": "26 to 35 years"},
        {"id": "age_3", "title": "36 to 45 years"},
        {"id": "age_4", "title": "46 to 55 years"},
        {"id": "age_5", "title": "56 to 65 years"},
        {"id": "age_6", "title": "65 years and above"}
    ]
    sections = [{"title": "Select age group", "rows": list_items}]
    message_response = whatsapp_client.send_list_message(phone_number, age_question, "Select Age Group", sections)
    if whatsapp_client.is_message_sent_successfully(message_response):
        return "OK", 200
    return "Failed to send age question", 500


def _send_place_of_birth_question(phone_number, whatsapp_client):
    """Send place of birth question."""
    place_question = "From which Emirate are you? Please select one:"
    list_items = [
        {"id": "place_abu_dhabi", "title": "Abu Dhabi"},
        {"id": "place_dubai", "title": "Dubai"},
        {"id": "place_sharjah", "title": "Sharjah"},
        {"id": "place_ajman", "title": "Ajman"},
        {"id": "place_umm_al_quwain", "title": "Umm Al Quwain"},
        {"id": "place_ras_al_khaimah", "title": "Ras Al Khaimah"},
        {"id": "place_fujairah", "title": "Fujairah"},
        {"id": "place_other", "title": "Other"}
    ]
    sections = [{"title": "Select Emirate", "rows": list_items}]
    message_response = whatsapp_client.send_list_message(phone_number, place_question, "Select Birthplace", sections)
    if whatsapp_client.is_message_sent_successfully(message_response):
        return "OK", 200
    return "Failed to send place of birth question", 500


def _handle_place_of_birth_response(parsed_message, user, whatsapp_client):
    """Handle place of birth response (list or text)."""
    if parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "list_reply":
        list_id = parsed_message.get("interactive_field_list_id")
        if list_id and list_id.startswith("place_"):
            place_value = list_id.split("_", 1)[1]
            if place_value != "other":
                user.place_of_birth = place_value
                db.session.commit()
                current_app.logger.info(f'User {parsed_message["from_field"]} answered place of birth: {place_value}')
                return _send_current_residence_question(parsed_message["from_field"], whatsapp_client)
            elif place_value == "other":
                other_question = "Please specify your place of birth."
                message_response = whatsapp_client.send_text_message(parsed_message["from_field"], other_question)
                if whatsapp_client.is_message_sent_successfully(message_response):
                    return "OK", 200
                return "Failed to send residence question", 500
        else:
            raise ValueError("All valid responses will have values of the form \"place_*\".")
    elif parsed_message.get("message_type") == "text":
        place_value = parsed_message.get("text_field")
        user.place_of_birth = place_value
        db.session.commit()
        current_app.logger.info(f'User {parsed_message["from_field"]} answered place of birth: {place_value}')
        return _send_current_residence_question(parsed_message["from_field"], whatsapp_client)
    else:
        raise ValueError("The only expected types of responses here are \"interactive\" \"list_reply\" and \"text\".")


def _send_current_residence_question(phone_number, whatsapp_client):
    """Send current residence question."""
    residence_question = "In which Emirate do you currently reside? (for scheduling and distribution purposes)"
    list_items = [
        {"id": "residence_abu_dhabi", "title": "Abu Dhabi"},
        {"id": "residence_dubai", "title": "Dubai"},
        {"id": "residence_sharjah", "title": "Sharjah"},
        {"id": "residence_ajman", "title": "Ajman"},
        {"id": "residence_umm_al_quwain", "title": "Umm Al Quwain"},
        {"id": "residence_ras_al_khaimah", "title": "Ras Al Khaimah"},
        {"id": "residence_fujairah", "title": "Fujairah"},
        {"id": "residence_other", "title": "Other"}
    ]
    sections = [{"title": "Select Emirate", "rows": list_items}]
    message_response = whatsapp_client.send_list_message(phone_number, residence_question, "Select Residence", sections)
    if whatsapp_client.is_message_sent_successfully(message_response):
        return "OK", 200
    return "Failed to send residence question", 500


def _handle_current_residence_response(parsed_message, user, whatsapp_client):
    """Handle current residence response (list or text)."""
    if parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "list_reply":
        list_id = parsed_message.get("interactive_field_list_id")
        if list_id and list_id.startswith("residence_"):
            place_value = list_id.split("_", 1)[1]
            if place_value != "other":
                user.current_residence = place_value
                db.session.commit()
                current_app.logger.info(f'User {parsed_message["from_field"]} answered current residence: {place_value}')
                return _send_optional_info_question(parsed_message["from_field"], whatsapp_client)
            elif place_value == "other":
                other_question = "Please specify your place of birth."
                message_response = whatsapp_client.send_text_message(parsed_message["from_field"], other_question)
                if whatsapp_client.is_message_sent_successfully(message_response):
                    return "OK", 200
                return "Failed to send residence question", 500
        else:
            raise ValueError("All valid responses will have values of the form \"residence_*\".")
    elif parsed_message.get("message_type") == "text":
        place_value = parsed_message.get("text_field")
        user.current_residence = place_value
        db.session.commit()
        current_app.logger.info(f'User {parsed_message["from_field"]} answered current residence: {place_value}')
        return _send_optional_info_question(parsed_message["from_field"], whatsapp_client)
    else:
        raise ValueError("The only expected types of responses here are \"interactive\" \"list_reply\" and \"text\".")


def _send_optional_info_question(phone_number, whatsapp_client):
    """Send optional name and contact question."""
    optional_question = "[Optional] Name:\n[Optional] Contact number:\n\nNote: Your name and contact number, if provided, will be stored with your data until August 31, 2027. After that, this information will be permanently deleted, and you won't be able to access your specific data by request.\n\nYou can reply with:\n- Just your name\n- Just your contact number\n- Both (name and contact on separate lines)\n- Or simply send \"No\" to skip this question."
    message_response = whatsapp_client.send_text_message(phone_number, optional_question)
    if whatsapp_client.is_message_sent_successfully(message_response):
        return "OK", 200
    return "Failed to send optional info request", 500


def _send_consent_question_1(phone_number, whatsapp_client):
    """Send first consent question."""
    consent_question_1 = "I agree to the use of my data for research and development purposes (including the extraction of linguistic features for building the dictionary and training AI models)."
    buttons = [
        {"type": "reply", "reply": {"id": "consent_required_yes", "title": "Yes"}},
        {"type": "reply", "reply": {"id": "consent_required_no", "title": "No"}}
    ]
    message_response = whatsapp_client.send_button_message(phone_number, consent_question_1, buttons)
    if whatsapp_client.is_message_sent_successfully(message_response):
        return "OK", 200
    return "Failed to send consent question 1", 500


def _send_consent_question_2(phone_number, whatsapp_client):
    """Send second consent question."""
    consent_question_2 = "[Optional] I agree to the archiving and sharing of my audio recordings with researchers and/or their release on public platforms."
    buttons = [
        {"type": "reply", "reply": {"id": "consent_optional_yes", "title": "Yes"}},
        {"type": "reply", "reply": {"id": "consent_optional_no", "title": "No"}}
    ]
    message_response = whatsapp_client.send_button_message(phone_number, consent_question_2, buttons)
    if whatsapp_client.is_message_sent_successfully(message_response):
        return "OK", 200
    return "Failed to send consent question 2", 500


def _send_consent_question_3(phone_number, whatsapp_client):
    """Send third consent question."""
    consent_question_3 = "I agree to the archiving the text transcripts derived from my audio recordings and sharing them with researchers and/or public platforms (with the audio itself not being shared)."
    buttons = [
        {"type": "reply", "reply": {"id": "consent_optional_alt_yes", "title": "Yes"}},
        {"type": "reply", "reply": {"id": "consent_optional_alt_no", "title": "No"}}
    ]
    message_response = whatsapp_client.send_button_message(phone_number, consent_question_3, buttons)
    if whatsapp_client.is_message_sent_successfully(message_response):
        return "OK", 200
    return "Failed to send consent question 3", 500


def _send_onboarding_completion(phone_number, whatsapp_client):
    """Send onboarding completion message."""
    completion_message = "Thank you! You have finished the onboarding process.\n\nWhenever you are ready to begin the survey, respond with any message."
    message_response = whatsapp_client.send_text_message(phone_number, completion_message)
    if whatsapp_client.is_message_sent_successfully(message_response):
        return "OK", 200
    return "Failed to send completion message", 500


def _send_survey_completion(user, phone_number):
    """Send survey completion messages with user ID and token."""
    whatsapp_client = WhatsAppClient()
    completion_message = f"Survey completed! Thank you for your responses.\n\n" + \
            f"If you'd like to delete your data later, please kaizoderp.com/manage_data and enter the following information."
    message_response = whatsapp_client.send_text_message(phone_number, completion_message)
    if whatsapp_client.is_message_sent_successfully(message_response):
        current_app.logger.info(f'Sent completion message 1 of 3 to {phone_number}')
    else:
        current_app.logger.error(f"Failed to send completion message 1 of 3 to {phone_number}: {message_response.status_code} - {message_response.text}")
    
    completion_message = f"User ID: {user.id}"
    message_response = whatsapp_client.send_text_message(phone_number, completion_message)
    if whatsapp_client.is_message_sent_successfully(message_response):
        current_app.logger.info(f'Sent completion message 2 of 3 to {phone_number}')
    else:
        current_app.logger.error(f"Failed to send completion message 2 of 3 to {phone_number}: {message_response.status_code} - {message_response.text}")
    
    completion_message = f"User Token: {user.token}"
    message_response = whatsapp_client.send_text_message(phone_number, completion_message)
    if whatsapp_client.is_message_sent_successfully(message_response):
        current_app.logger.info(f'Sent completion message 3 of 3 to {phone_number}')
    else:
        current_app.logger.error(f"Failed to send completion message 3 of 3 to {phone_number}: {message_response.status_code} - {message_response.text}")
    
    return "Survey completed!", 200

