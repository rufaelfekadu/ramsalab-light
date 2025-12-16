import hashlib
import hmac
import json
import os
import random
import re
import time
from requests import post

from flask import (
    Blueprint, current_app, render_template, request, redirect,
    url_for, jsonify, flash, session, send_from_directory
)
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.sql.expression import func
from sqlalchemy.orm import joinedload

from app import csrf
from app.utils import (
    allowed_file, 
    save_audio_file,
    _create_web_response,
    _handle_web_survey_logic
)
from app.whatsapp_utils import (
    WhatsAppClient,
    WhatsAppMediaHandler,
    _parse_whatsapp_message,
    _create_whatsapp_response_from_message,
    _handle_whatsapp_survey_logic
) 
from app.database import db
from app.models import QuestionGroup, User, Question, Response, Survey, Progress


def create_new_user_token():
    import uuid
    token = str(uuid.uuid4())
    if User.query.filter_by(token=token).first():
        return create_new_user_token()
    return token

def token_to_6_digits(token, secret_key=None):
    """
    Convert a token string to a secure 6-digit number using HMAC.
    Uses HMAC-SHA256 with a secret key to prevent reverse engineering.
    
    Security features:
    - HMAC prevents hash reversal without the secret key
    - Deterministic: same token always produces same 6-digit code
    - Secret key from app config prevents unauthorized generation
    
    Args:
        token: The user token to hash
        secret_key: Secret key for HMAC (if None, will use app config)
    
    Returns:
        A 6-digit string (000000-999999) that is deterministic but secure
    """
    if not token:
        return None
    
    # Get secret key from app config if not provided
    if secret_key is None:
        try:
            from flask import current_app
            secret_key = current_app.config.get('SECRET_KEY')
            if not secret_key:
                # Fallback to environment variable
                secret_key = os.environ.get('SECRET_KEY')
        except RuntimeError:
            # If outside app context, use environment variable
            secret_key = os.environ.get('SECRET_KEY')
    
    # Ensure we have a secret key
    if not secret_key:
        raise ValueError("SECRET_KEY must be configured in app config or environment")
    
    # Ensure secret_key is bytes
    if isinstance(secret_key, str):
        secret_key = secret_key.encode('utf-8')
    elif not isinstance(secret_key, bytes):
        secret_key = str(secret_key).encode('utf-8')
    
    # Use HMAC-SHA256 for secure hashing
    # HMAC ensures the hash cannot be reversed without the secret key
    # This prevents attackers from generating valid 6-digit codes without the secret
    hmac_obj = hmac.new(secret_key, token.encode('utf-8'), hashlib.sha256)
    hash_hex = hmac_obj.hexdigest()
    
    # Convert to integer and take modulo to get 6 digits
    # Using modulo 1000000 gives us exactly 1,000,000 possible values (000000-999999)
    hash_int = int(hash_hex, 16)
    six_digit = hash_int % 1000000
    
    # Format as 6 digits with leading zeros
    return f"{six_digit:06d}"

bp = Blueprint("routes", __name__)


def update_user_ids_if_logged_in(current_user_id):
    """
    Helper function to update the logged-in user's user_ids field
    if the current_user_id is not already in the list.
    """
    if current_user.is_authenticated and current_user_id:
        try:
            # Get existing user_ids or initialize as empty string
            existing_user_ids = current_user.user_ids or ""
            # Split by comma and filter out empty strings
            user_ids_list = [uid.strip() for uid in existing_user_ids.split(",") if uid.strip()]
            # Check if current_user_id is not already in the list
            if str(current_user_id) not in user_ids_list:
                # Append the new user_id
                if user_ids_list:
                    current_user.user_ids = existing_user_ids + "," + str(current_user_id)
                else:
                    current_user.user_ids = str(current_user_id)
                db.session.commit()
                current_app.logger.info(f'Updated user_ids for user {current_user.id}: added {current_user_id}')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating user_ids: {e}", exc_info=True)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Login route"""
    if current_user.is_authenticated:
        return redirect(url_for("routes.dashboard"))
    
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        if not username or not password:
            flash("Please provide both username and password.", "error")
            return render_template("login.html")
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user, remember=request.form.get("remember", False))
            
            # Check for user_id and update user_ids field if needed
            # Check query param, form data, and session
            current_user_id = request.args.get("user_id") or request.form.get("user_id") or session.get("user_id")
            if current_user_id:
                try:
                    # Validate and convert to string for consistent comparison
                    user_id_int = int(current_user_id)
                    current_user_id = str(user_id_int)
                    
                    # Get existing user_ids or initialize as empty string
                    existing_user_ids = user.user_ids or ""
                    # Split by comma and filter out empty strings
                    user_ids_list = [uid.strip() for uid in existing_user_ids.split(",") if uid.strip()]
                    # Check if current_user_id is not already in the list
                    if current_user_id not in user_ids_list:
                        # Append the new user_id
                        if user_ids_list:
                            user.user_ids = existing_user_ids + "," + current_user_id
                        else:
                            user.user_ids = current_user_id
                        db.session.commit()
                        current_app.logger.info(f'Added user_id {current_user_id} to user {user.id}\'s user_ids field. New user_ids: {user.user_ids}')
                    else:
                        current_app.logger.info(f'User_id {current_user_id} already in user {user.id}\'s user_ids field')
                except (ValueError, TypeError):
                    current_app.logger.warning(f'Invalid user_id format during login: {current_user_id}')
            
            # Determine redirect destination
            next_page = request.args.get("next")
            if next_page:
                response = redirect(next_page)
            else:
                # Redirect to dashboard after login
                response = redirect(url_for("routes.dashboard"))
            
            # Set consent_confirmation cookie if user has consent in database
            if user.consent_required:
                # Set cookie to expire in 1 year
                response.set_cookie('consent_confirmation', 'true', max_age=365*24*60*60, path='/')
            
            return response
        else:
            flash("Invalid username or password.", "error")
    
    # Pass user_id to template if present in query params
    user_id = request.args.get("user_id")
    return render_template("login.html", user_id=user_id)


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Register route"""
    if current_user.is_authenticated:
        return redirect(url_for("routes.dashboard"))
    
    if request.method == "POST":
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        password_confirm = request.form.get("password_confirm")
        
        # Validation
        if not username or not password:
            flash("Please provide both username and password.", "error")
            return render_template("register.html")
        
        if password != password_confirm:
            flash("Passwords do not match.", "error")
            return render_template("register.html")
        
        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template("register.html")
        
        # Check if username already exists
        if User.query.filter_by(username=username).first():
            flash("Username already exists. Please choose a different one.", "error")
            return render_template("register.html")
        
        # Check if email already exists (if provided)
        if email and User.query.filter_by(email=email).first():
            flash("Email already registered. Please use a different email.", "error")
            return render_template("register.html")
        
        # Create new user
        try:
            # Check for consent_confirmation cookie
            consent_cookie = request.cookies.get('consent_confirmation')
            has_consent = consent_cookie == 'true'
            
            # Check for user_id in URL parameter or form data
            current_user_id = request.args.get("user_id") or request.form.get("user_id")
            
            # Convert to string and validate
            user_ids_value = None
            source_user = None
            if current_user_id:
                try:
                    # Validate it's a valid integer
                    user_id_int = int(current_user_id)
                    user_ids_value = str(user_id_int)
                    current_app.logger.info(f'Registering user with user_id: {user_ids_value}')
                    
                    # Find the source user to copy fields from
                    source_user = User.query.filter_by(id=user_id_int).first()
                    if source_user:
                        current_app.logger.info(f'Found source user {source_user.id} to copy fields from')
                    else:
                        current_app.logger.warning(f'Source user with id {user_id_int} not found')
                except (ValueError, TypeError):
                    current_app.logger.warning(f'Invalid user_id format during registration: {current_user_id}')
                    current_user_id = None
            
            user = User(
                username=username,
                email=email if email else None,
                token=create_new_user_token(),
                user_ids=user_ids_value
            )
            
            # Copy fields from source user if it exists
            if source_user:
                user.survey_name = source_user.survey_name
                user.last_prompt_sent = source_user.last_prompt_sent
                user.emirati_citizenship = source_user.emirati_citizenship
                user.age_group = source_user.age_group
                user.place_of_birth = source_user.place_of_birth
                user.current_residence = source_user.current_residence
                user.real_name_optional_input = source_user.real_name_optional_input
                user.phone_number_optional_input = source_user.phone_number_optional_input
                user.consent_read_form = source_user.consent_read_form
                user.consent_required = source_user.consent_required
                user.consent_optional = source_user.consent_optional
                user.consent_optional_alternative = source_user.consent_optional_alternative
                current_app.logger.info(f'Copied fields from source user {source_user.id} to new user')
            
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            
            current_app.logger.info(f'Created new user {user.id} with user_ids: {user.user_ids}')
            
            flash("Registration successful! Please log in.", "success")
            
            # If user_id was provided, include it in the redirect to login
            if current_user_id:
                return redirect(url_for("routes.login", user_id=current_user_id))
            else:
                return redirect(url_for("routes.login"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Registration error: {e}", exc_info=True)
            flash("An error occurred during registration. Please try again.", "error")
    
    # Pass user_id to template if present in query params
    user_id = request.args.get("user_id")
    return render_template("register.html", user_id=user_id)


@bp.route("/update_consent", methods=["POST"])
def update_consent():
    """Update user's consent field in database"""
    try:
        # Get consent values from form - support both old and new field names for backward compatibility
        consent_read_form = request.form.get("consent_read_form") == "on" or request.form.get("consent_read") == "on"
        consent_required = request.form.get("consent_required") == "on" or request.form.get("consent_audio") == "on"
        consent_optional = request.form.get("consent_optional") == "on" or request.form.get("consent_data") == "on"
        consent_optional_alternative = request.form.get("consent_optional_alternative") == "on"
        
        # Validate required consents (consent_read_form, consent_required, and consent_optional_alternative are required)
        if not (consent_read_form and consent_required and (consent_optional or consent_optional_alternative)):
            flash("يرجى الموافقة على جميع الإقرارات المطلوبة (المميزة بـ *)", "error")
            return redirect(url_for('routes.consent_form'))
        
        # Get or create user
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Create anonymous user or get from session
            user_id = session.get("user_id")
            if user_id:
                try:
                    user_id = int(user_id)
                    user = User.query.filter_by(id=user_id).first()
                except (ValueError, TypeError):
                    pass
            
            if not user:
                # Create new anonymous user with token
                token = create_new_user_token()
                user = User(username=f"user_{token[:8]}", token=token)
                db.session.add(user)
                db.session.flush()  # Get the user ID
                session['user_id'] = user.id
        
        # Ensure token exists
        if not user.token:
            user.token = create_new_user_token()
        
        # Update consent fields
        user.consent_read_form = consent_read_form
        user.consent_required = consent_required
        user.consent_optional = consent_optional
        user.consent_optional_alternative = consent_optional_alternative
        
        db.session.commit()
        current_app.logger.info(f'Updated consent for user {user.id}')
        
        # Redirect to demography page
        return redirect(url_for('routes.demography'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating consent: {e}", exc_info=True)
        flash("حدث خطأ أثناء حفظ البيانات. يرجى المحاولة مرة أخرى.", "error")
        return redirect(url_for('routes.consent_form'))


@bp.route("/update_emirati_citizenship", methods=["POST"])
def update_emirati_citizenship():
    """Update user's emirati_citizenship field in database"""
    try:
        emirati_citizenship = request.form.get("emirati_citizenship")
        if emirati_citizenship is None:
            return jsonify({"status": "error", "message": "emirati_citizenship parameter is required"}), 400
        
        # Convert string to boolean
        is_emirati = emirati_citizenship.lower() == 'true'
        
        # Get user - either from current_user if logged in, or from user_id parameter
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Get user_id from form data or URL parameter
            user_id = request.form.get("user_id") or request.args.get("user_id")
            if not user_id:
                return jsonify({"status": "error", "message": "User ID is required when not logged in"}), 400
            
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid user ID format"}), 400
        
        # Update the user's emirati_citizenship field
        user.emirati_citizenship = is_emirati
        db.session.commit()
        current_app.logger.info(f'Updated emirati_citizenship for user {user.id}: {is_emirati}')
        return jsonify({"status": "success", "message": "Emirati citizenship updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating emirati_citizenship: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to update emirati citizenship"}), 500


@bp.route("/update_age_group", methods=["POST"])
def update_age_group():
    """Update user's age_group field in database"""
    try:
        age_group = request.form.get("age_group")
        if age_group is None:
            return jsonify({"status": "error", "message": "age_group parameter is required"}), 400
        
        # Convert to integer
        try:
            age_group_int = int(age_group)
            if age_group_int < 1 or age_group_int > 6:
                return jsonify({"status": "error", "message": "age_group must be between 1 and 6"}), 400
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "Invalid age_group format"}), 400
        
        # Get user - either from current_user if logged in, or from user_id parameter
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Get user_id from form data or URL parameter
            user_id = request.form.get("user_id") or request.args.get("user_id")
            if not user_id:
                return jsonify({"status": "error", "message": "User ID is required when not logged in"}), 400
            
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid user ID format"}), 400
        
        # Update the user's age_group field
        user.age_group = age_group_int
        db.session.commit()
        current_app.logger.info(f'Updated age_group for user {user.id}: {age_group_int}')
        return jsonify({"status": "success", "message": "Age group updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating age_group: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to update age group"}), 500


@bp.route("/update_place_of_birth", methods=["POST"])
def update_place_of_birth():
    """Update user's place_of_birth field in database"""
    try:
        place_of_birth = request.form.get("place_of_birth")
        if not place_of_birth or not place_of_birth.strip():
            return jsonify({"status": "error", "message": "place_of_birth parameter is required"}), 400
        
        place_of_birth = place_of_birth.strip()
        
        # Get user - either from current_user if logged in, or from user_id parameter
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Get user_id from form data or URL parameter
            user_id = request.form.get("user_id") or request.args.get("user_id")
            if not user_id:
                return jsonify({"status": "error", "message": "User ID is required when not logged in"}), 400
            
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid user ID format"}), 400
        
        # Update the user's place_of_birth field
        user.place_of_birth = place_of_birth
        db.session.commit()
        current_app.logger.info(f'Updated place_of_birth for user {user.id}: {place_of_birth}')
        return jsonify({"status": "success", "message": "Place of birth updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating place_of_birth: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to update place of birth"}), 500


@bp.route("/update_current_residence", methods=["POST"])
def update_current_residence():
    """Update user's current_residence field in database"""
    try:
        current_residence = request.form.get("current_residence")
        if not current_residence or not current_residence.strip():
            return jsonify({"status": "error", "message": "current_residence parameter is required"}), 400
        
        current_residence = current_residence.strip()
        
        # Get user - either from current_user if logged in, or from user_id parameter
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Get user_id from form data or URL parameter
            user_id = request.form.get("user_id") or request.args.get("user_id")
            if not user_id:
                return jsonify({"status": "error", "message": "User ID is required when not logged in"}), 400
            
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid user ID format"}), 400
        
        # Update the user's current_residence field
        user.current_residence = current_residence
        db.session.commit()
        current_app.logger.info(f'Updated current_residence for user {user.id}: {current_residence}')
        return jsonify({"status": "success", "message": "Current residence updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating current_residence: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to update current residence"}), 500


@bp.route("/update_optional_info", methods=["POST"])
def update_optional_info():
    """Update user's optional name and contact number fields in database"""
    try:
        real_name = request.form.get("real_name_optional_input", "").strip()
        phone_number = request.form.get("phone_number_optional_input", "").strip()
        
        # Get user - either from current_user if logged in, or from user_id parameter
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Get user_id from form data or URL parameter
            user_id = request.form.get("user_id") or request.args.get("user_id")
            if not user_id:
                return jsonify({"status": "error", "message": "User ID is required when not logged in"}), 400
            
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid user ID format"}), 400
        
        # Update the user's optional fields (only if values are provided)
        if real_name:
            user.real_name_optional_input = real_name
        if phone_number:
            user.phone_number_optional_input = phone_number
        
        db.session.commit()
        current_app.logger.info(f'Updated optional info for user {user.id}: name={bool(real_name)}, phone={bool(phone_number)}')
        return jsonify({"status": "success", "message": "Optional info updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating optional info: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to update optional info"}), 500


@bp.route("/update_consent_options", methods=["POST"])
def update_consent_options():
    """Update user's consent options fields in database"""
    try:
        # Get checkbox values from form
        consent_read_form = request.form.get("consent_read_form", "false").lower() == 'true'
        consent_required = request.form.get("consent_required", "false").lower() == 'true'
        consent_optional = request.form.get("consent_optional", "false").lower() == 'true'
        consent_optional_alternative = request.form.get("consent_optional_alternative", "false").lower() == 'true'
        
        # Get user - either from current_user if logged in, or from user_id parameter
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Get user_id from form data or URL parameter
            user_id = request.form.get("user_id") or request.args.get("user_id")
            if not user_id:
                return jsonify({"status": "error", "message": "User ID is required when not logged in"}), 400
            
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid user ID format"}), 400
        
        # Update the user's consent options fields
        user.consent_read_form = consent_read_form
        user.consent_required = consent_required
        user.consent_optional = consent_optional
        user.consent_optional_alternative = consent_optional_alternative
        
        db.session.commit()
        current_app.logger.info(f'Updated consent options for user {user.id}: read_form={consent_read_form}, required={consent_required}, optional={consent_optional}, optional_alt={consent_optional_alternative}')
        return jsonify({"status": "success", "message": "Consent options updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating consent options: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to update consent options"}), 500


@bp.route("/update_all_consent_data", methods=["POST"])
def update_all_consent_data():
    """Update all consent and demographic data in database at once"""
    try:
        # Get user - either from current_user if logged in, or from user_id parameter
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Get user_id from form data or URL parameter
            user_id = request.form.get("user_id") or request.args.get("user_id")
            if not user_id:
                return jsonify({"status": "error", "message": "User ID is required when not logged in"}), 400
            
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid user ID format"}), 400
        
        # Update emirati citizenship if provided
        if "emirati_citizenship" in request.form:
            emirati_citizenship = request.form.get("emirati_citizenship", "false").lower() == 'true'
            user.emirati_citizenship = emirati_citizenship
        
        # Update age group if provided
        if "age_group" in request.form:
            try:
                age_group = int(request.form.get("age_group"))
                if 1 <= age_group <= 6:
                    user.age_group = age_group
            except (ValueError, TypeError):
                pass  # Skip invalid age group values
        
        # Update place of birth if provided
        if "place_of_birth" in request.form:
            place_of_birth = request.form.get("place_of_birth", "").strip()
            if place_of_birth:
                user.place_of_birth = place_of_birth
        
        # Update current residence if provided
        if "current_residence" in request.form:
            current_residence = request.form.get("current_residence", "").strip()
            if current_residence:
                user.current_residence = current_residence
        
        # Update optional name if provided
        if "real_name_optional_input" in request.form:
            real_name = request.form.get("real_name_optional_input", "").strip()
            if real_name:
                user.real_name_optional_input = real_name
        
        # Update optional phone number if provided
        if "phone_number_optional_input" in request.form:
            phone_number = request.form.get("phone_number_optional_input", "").strip()
            if phone_number:
                user.phone_number_optional_input = phone_number
        
        # Update consent options if provided
        if "consent_read_form" in request.form:
            consent_read_form = request.form.get("consent_read_form", "false").lower() == 'true'
            user.consent_read_form = consent_read_form
        
        if "consent_required" in request.form:
            consent_required = request.form.get("consent_required", "false").lower() == 'true'
            user.consent_required = consent_required
        
        if "consent_optional" in request.form:
            consent_optional = request.form.get("consent_optional", "false").lower() == 'true'
            user.consent_optional = consent_optional
        
        if "consent_optional_alternative" in request.form:
            consent_optional_alternative = request.form.get("consent_optional_alternative", "false").lower() == 'true'
            user.consent_optional_alternative = consent_optional_alternative
        
        db.session.commit()
        current_app.logger.info(f'Updated all consent data for user {user.id}')
        return jsonify({"status": "success", "message": "All consent data updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating all consent data: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to update consent data"}), 500


@bp.route("/logout")
@login_required
def logout():
    """Logout route"""
    logout_user()
    
    # Unset the consent_confirmation cookie
    response = redirect(url_for("routes.login"))
    response.set_cookie('consent_confirmation', '', max_age=0, path='/')
    
    flash("You have been logged out successfully.", "success")
    return response


@bp.route("/participant-information")
def participant_information():
    """Display the Participant Information Sheet"""
    return render_template("participant_information.html")

@bp.route("/")
def index():
    """Landing page"""
    return render_template("index.html", current_user=current_user)


@bp.route("/consent-form")
def consent_form():
    """Consent form page"""
    # Get or create user when they visit the consent form
    user = None
    if current_user.is_authenticated:
        user = current_user
    else:
        # Get user from session or create new anonymous user
        user_id = session.get("user_id")
        if user_id:
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
            except (ValueError, TypeError):
                pass
        
        if not user:
            # Create new anonymous user with token
            token = create_new_user_token()
            user = User(username=f"user_{token[:8]}", token=token)
            db.session.add(user)
            db.session.flush()  # Get the user ID
            session['user_id'] = user.id
            db.session.commit()
            current_app.logger.info(f'Created new user {user.id} with token on consent form visit')
    
    return render_template("consent-form.html", current_user=current_user)


@bp.route("/demography")
def demography():
    """Demographic information collection page"""
    # Verify user exists in session
    user = None
    if current_user.is_authenticated:
        user = current_user
    else:
        user_id = session.get("user_id")
        if user_id:
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
            except (ValueError, TypeError):
                pass
    
    if not user:
        flash("يرجى البدء من الصفحة الرئيسية.", "error")
        return redirect(url_for('routes.index'))
    
    return render_template("demography.html", current_user=current_user)


@bp.route("/select-theme", methods=["GET", "POST"])
def select_theme():
    """Theme/category selection page - displays surveys from database"""
    
    # Handle POST requests: validate user and survey, update progress
    if request.method == "POST":
        survey_id = request.form.get('survey_id')
        
        if not survey_id:
            flash("يرجى اختيار استطلاع.", "error")
            return redirect(url_for('routes.select_theme'))
        
        try:
            survey_id = int(survey_id)
            survey = Survey.query.filter_by(id=survey_id).first()
            
            # Validate survey exists
            if not survey:
                flash("الاستطلاع المحدد غير موجود.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Validate and get user
            user = None
            if current_user.is_authenticated:
                user = current_user
            else:
                # Get user from session or create new anonymous user
                user_id = session.get("user_id")
                if user_id:
                    try:
                        user_id = int(user_id)
                        user = User.query.filter_by(id=user_id).first()
                    except (ValueError, TypeError):
                        pass

            
            # Validate user exists
            if not user:
                flash("حدث خطأ في التحقق من المستخدم.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Get or create progress entry for this user and survey
            progress = Progress.query.filter_by(
                user_id=user.id,
                survey_id=survey.id
            ).first()
            
            # Get all questions for the survey
            all_questions = Question.query.filter_by(survey_id=survey.id).all()
            
            if not all_questions:
                flash("لا توجد أسئلة في هذا الاستطلاع.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Get seen question IDs from session
            seen_key = f'seen_questions_{survey_id}'
            seen_ids = session.get(seen_key, [])
            
            # Get all question IDs
            all_question_ids = [q.id for q in all_questions]
            
            if progress:
                # Get current question from progress
                current_question = Question.query.filter_by(
                    id=progress.current_question_id,
                    survey_id=survey.id
                ).first() if progress.current_question_id else None
                
                # If current question exists, mark it as seen if not already in the list
                if current_question and current_question.id not in seen_ids:
                    seen_ids.append(current_question.id)
                    session[seen_key] = seen_ids
                
                # If current question doesn't exist (maybe deleted), select a random unseen question
                if not current_question:
                    # Filter out seen questions
                    unseen_ids = [qid for qid in all_question_ids if qid not in seen_ids]
                    
                    # If no unseen questions remain, reset and start fresh
                    if not unseen_ids:
                        seen_ids = []
                        unseen_ids = all_question_ids
                    
                    # Randomly select one question from unseen questions
                    if unseen_ids:
                        selected_question_id = random.choice(unseen_ids)
                        current_question = Question.query.filter_by(id=selected_question_id).first()
                        
                        # Mark selected question as seen in session
                        if current_question:
                            seen_ids.append(selected_question_id)
                            session[seen_key] = seen_ids
                            
                            # Update progress with new current question
                            progress.current_question_id = current_question.id
            else:
                # Create new progress entry - randomly select first question
                # Filter out seen questions
                unseen_ids = [qid for qid in all_question_ids if qid not in seen_ids]
                
                # If no unseen questions remain, reset and start fresh
                if not unseen_ids:
                    seen_ids = []
                    unseen_ids = all_question_ids
                
                # Randomly select one question from unseen questions
                if not unseen_ids:
                    flash("لا توجد أسئلة في هذا الاستطلاع.", "error")
                    return redirect(url_for('routes.select_theme'))
                
                selected_question_id = random.choice(unseen_ids)
                current_question = Question.query.filter_by(id=selected_question_id).first()
                
                if not current_question:
                    flash("لا توجد أسئلة في هذا الاستطلاع.", "error")
                    return redirect(url_for('routes.select_theme'))
                
                # Mark selected question as seen in session
                seen_ids.append(selected_question_id)
                session[seen_key] = seen_ids
                
                # Create progress entry
                progress = Progress(
                    user_id=user.id,
                    survey_id=survey.id,
                    current_question_id=current_question.id
                )
                db.session.add(progress)
            
            db.session.commit()
            current_app.logger.info(f'Updated progress for user {user.id} on survey {survey.id}, current question: {current_question.id} (random selection)')
            
            # Redirect to record page with the current question
            return redirect(url_for('routes.record', 
                                  question_id=current_question.id,
                                  survey_id=survey.id,
                                  prompt_text=current_question.prompt))
            
        except (ValueError, TypeError) as e:
            current_app.logger.error(f"Invalid survey_id: {e}", exc_info=True)
            flash("معرف الاستطلاع غير صحيح.", "error")
            return redirect(url_for('routes.select_theme'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating progress: {e}", exc_info=True)
            flash("حدث خطأ أثناء تحديث التقدم. يرجى المحاولة مرة أخرى.", "error")
            return redirect(url_for('routes.select_theme'))
    
    # Handle GET requests: fetch and display all surveys
    # Verify user exists in session
    user = None
    if current_user.is_authenticated:
        user = current_user
    else:
        user_id = session.get("user_id")
        if user_id:
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
            except (ValueError, TypeError):
                pass
    
    if not user:
        flash("يرجى البدء من الصفحة الرئيسية.", "error")
        return redirect(url_for('routes.index'))
    
    try:
        surveys = Survey.query.order_by(Survey.id).all()
        current_app.logger.info(f'Fetched {len(surveys)} surveys for select-theme page')
    except Exception as e:
        current_app.logger.error(f"Error fetching surveys: {e}", exc_info=True)
        surveys = []
        flash("حدث خطأ أثناء تحميل الاستطلاعات.", "error")
    
    return render_template("select-theme.html", 
                         surveys=surveys,
                         current_user=current_user)


@bp.route("/record")
def record():
    """Audio recording page"""
    question_id = request.args.get('question_id')
    survey_id = request.args.get('survey_id')
    theme = request.args.get('theme', 'general')
    prompt_text = request.args.get('prompt', 'يرجى قراءة النص التالي بصوت واضح...')
    
    # Verify user exists in session
    user = None
    if current_user.is_authenticated:
        user = current_user
    else:
        user_id = session.get("user_id")
        if user_id:
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
            except (ValueError, TypeError):
                pass
    
    if not user:
        flash("يرجى البدء من الصفحة الرئيسية.", "error")
        return redirect(url_for('routes.index'))
    
    # If question_id is provided, fetch the question from database
    if question_id:
        try:
            question_id = int(question_id)
            question = Question.query.filter_by(id=question_id).first()
            if question:
                prompt_text = question.prompt
                if survey_id:
                    theme = f"survey_{survey_id}"
        except (ValueError, TypeError):
            pass
    
    return render_template("record.html", 
                         theme=theme, 
                         prompt_text=prompt_text,
                         question_id=question_id,
                         survey_id=survey_id,
                         current_user=current_user)


@bp.route("/change-question", methods=["POST"])
def change_question():
    """Skip current question and load next question"""
    try:
        survey_id = request.form.get('survey_id') or request.args.get('survey_id')
        current_question_id = request.form.get('question_id') or request.args.get('question_id')
        
        if not survey_id:
            flash("معرف الاستطلاع مطلوب.", "error")
            return redirect(url_for('routes.select_theme'))
        
        # Get user from session
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            user_id = session.get("user_id")
            if user_id:
                try:
                    user_id = int(user_id)
                    user = User.query.filter_by(id=user_id).first()
                except (ValueError, TypeError):
                    pass
        
        if not user:
            flash("يرجى البدء من الصفحة الرئيسية.", "error")
            return redirect(url_for('routes.index'))
        
        try:
            survey_id = int(survey_id)
            survey = Survey.query.filter_by(id=survey_id).first()
            
            if not survey:
                flash("الاستطلاع المحدد غير موجود.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Get or create progress entry
            progress = Progress.query.filter_by(
                user_id=user.id,
                survey_id=survey.id
            ).first()
            
            if not progress:
                flash("لم يتم العثور على التقدم. يرجى اختيار استطلاع مرة أخرى.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Get current question for exclusion
            current_question = None
            current_question_id_int = None
            if current_question_id:
                try:
                    current_question_id_int = int(current_question_id)
                    current_question = Question.query.filter_by(
                        id=current_question_id_int,
                        survey_id=survey.id
                    ).first()
                except (ValueError, TypeError):
                    pass
            
            # Get all questions for the survey
            all_questions = Question.query.filter_by(survey_id=survey.id).all()
            
            if not all_questions:
                flash("لا توجد أسئلة متاحة في هذا الاستطلاع.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Get seen question IDs from session
            seen_key = f'seen_questions_{survey_id}'
            seen_ids = session.get(seen_key, [])
            
            # Get all question IDs
            all_question_ids = [q.id for q in all_questions]
            
            # Filter out seen questions and current question
            unseen_ids = [qid for qid in all_question_ids 
                         if qid not in seen_ids and qid != current_question_id_int]
            
            # If no unseen questions remain, reset and start fresh
            if not unseen_ids:
                seen_ids = []
                unseen_ids = [qid for qid in all_question_ids if qid != current_question_id_int]
            
            # # Randomly select one question from unseen questions
            if not unseen_ids:
                flash("لا توجد أسئلة متاحة في هذا الاستطلاع.", "error")
                unseen_ids = [current_question_id_int]
            
            next_question_id = random.choice(unseen_ids)
            
            # Mark selected question as seen in session
            seen_ids.append(next_question_id)
            session[seen_key] = seen_ids
            
            # Get the next question object
            next_question = Question.query.filter_by(id=next_question_id).first()
            
            if not next_question:
                flash("لا توجد أسئلة متاحة في هذا الاستطلاع.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Update progress
            progress.current_question_id = next_question.id
            db.session.commit()
            
            current_app.logger.info(f'Changed question for user {user.id} in survey {survey.id} to question {next_question.id} (random selection)')
            
            # Redirect to record page with new question
            return redirect(url_for('routes.record', 
                                  question_id=next_question.id,
                                  survey_id=survey.id,
                                  prompt_text=next_question.prompt))
            
        except (ValueError, TypeError) as e:
            current_app.logger.error(f"Invalid survey_id or question_id: {e}", exc_info=True)
            flash("معرف غير صحيح.", "error")
            return redirect(url_for('routes.select_theme'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error changing question: {e}", exc_info=True)
            flash("حدث خطأ أثناء تغيير السؤال. يرجى المحاولة مرة أخرى.", "error")
            return redirect(url_for('routes.select_theme'))
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in change_question: {e}", exc_info=True)
        flash("حدث خطأ. يرجى المحاولة مرة أخرى.", "error")
        return redirect(url_for('routes.select_theme'))


@bp.route("/thanks")
def thanks():
    """Thank you page"""
    # Get user from session
    user = None
    if current_user.is_authenticated:
        user = current_user
    else:
        user_id = session.get("user_id")
        if user_id:
            try:
                user_id = int(user_id)
                user = User.query.filter_by(id=user_id).first()
            except (ValueError, TypeError):
                pass
    
    token = user.token if user else None
    token_6_digits = token_to_6_digits(token) if token else None
    
    return render_template("thanks.html", 
                         current_user=current_user,
                         user_token=token,
                         token_6_digits=token_6_digits)


@bp.route("/update_demography", methods=["POST"])
def update_demography():
    """Update user's demographic information"""
    try:
        # Get form data
        emirati_citizenship = request.form.get("emirati_citizenship")
        age_group = request.form.get("age_group")
        gender = request.form.get("gender")
        place_of_birth = request.form.get("place_of_birth")
        current_residence = request.form.get("current_residence")
        real_name = request.form.get("real_name")
        dialect_description = request.form.get("dialect_description")
        
        # Get or create user
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            # Create anonymous user or get from session
            user_id = request.form.get("user_id") or session.get("user_id")
            if user_id:
                try:
                    user_id = int(user_id)
                    user = User.query.filter_by(id=user_id).first()
                except (ValueError, TypeError):
                    pass
            
            if not user:
                # Create new anonymous user with token
                token = create_new_user_token()
                user = User(username=f"user_{token[:8]}", token=token)
                db.session.add(user)
                db.session.flush()  # Get the user ID
                session['user_id'] = user.id
        
        # Ensure token exists
        if not user.token:
            user.token = create_new_user_token()
        
        # Update demographic fields
        if emirati_citizenship:
            user.emirati_citizenship = emirati_citizenship.lower() == 'yes'
        if age_group:
            # Store age_group as string for now, or convert to integer if needed
            # The model expects Integer, but we're getting string values like "18-25"
            # For now, we'll store it as a string in a text field or handle conversion
            # Since age_group is Integer in model, we'll need to map string to int
            age_mapping = {
                "18-25": 1,
                "26-35": 2,
                "36-45": 3,
                "46-55": 4,
                "56-65": 5,
                "65+": 6
            }
            user.age_group = age_mapping.get(age_group, None)
        # Note: gender field doesn't exist in User model, skipping for now
        # if gender:
        #     user.gender = gender
        if place_of_birth:
            user.place_of_birth = place_of_birth
        if current_residence:
            user.current_residence = current_residence
        if real_name:
            user.real_name_optional_input = real_name
        
        if dialect_description:
            user.dialect_description = dialect_description
        
        db.session.commit()
        current_app.logger.info(f'Updated demography for user {user.id}')
        
        # Redirect to select theme page
        return redirect(url_for('routes.select_theme'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating demography: {e}", exc_info=True)
        flash("حدث خطأ أثناء حفظ البيانات. يرجى المحاولة مرة أخرى.", "error")
        return redirect(url_for('routes.demography'))


@bp.route("/dashboard")
@login_required
def dashboard():
    """Dashboard for logged-in users showing all their responses"""
    # Get all user_ids associated with this account
    user_ids_to_query = [current_user.id]  # Start with the logged-in user's ID
    
    # Add any user_ids from the user_ids field (these are survey session IDs)
    if current_user.user_ids:
        user_ids_list = [uid.strip() for uid in current_user.user_ids.split(",") if uid.strip()]
        # Convert to integers and add to list
        for uid_str in user_ids_list:
            try:
                uid_int = int(uid_str)
                if uid_int not in user_ids_to_query:
                    user_ids_to_query.append(uid_int)
            except ValueError:
                continue
    
    current_app.logger.info(f'Querying responses for user_ids: {user_ids_to_query} (user {current_user.id})')
    
    # Get all responses for this user (from all their survey sessions)
    if user_ids_to_query:
        responses = Response.query.filter(
            Response.user_id.in_(user_ids_to_query)
        ).order_by(Response.timestamp.desc()).all()
        current_app.logger.info(f'Found {len(responses)} responses for user {current_user.id} across {len(user_ids_to_query)} user_ids')
    else:
        responses = []
        current_app.logger.info(f'No user_ids to query for user {current_user.id}')
    
    # Prepare data for display
    responses_data = []
    for response in responses:
        question = response.question
        survey = question.survey if question else None
        
        response_info = {
            "id": response.id,
            "question_id": response.question_id,
            "question_prompt": question.prompt if question else "Unknown Question",
            "question_type": question.question_type if question else "unknown",
            "response_type": response.response_type,
            "response_value": response.response_value,
            "file_path": response.file_path,
            "timestamp": response.timestamp,
            "survey_name": survey.name if survey else "Unknown Survey"
        }
        responses_data.append(response_info)
    
    return render_template("dashboard.html", 
                         responses=responses_data, 
                         current_user=current_user,
                         total_responses=len(responses_data))


@bp.route("/uploads/<path:question_id>/<path:filename>")
def serve_upload(question_id, filename):
    """Serve uploaded files"""
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    question_folder = os.path.join(upload_folder, question_id)
    return send_from_directory(question_folder, filename)


@bp.route("/delete_survey_data", methods=["POST"])
def delete_survey_data():
    """Delete all responses for a specific survey session (user_id)"""
    # Try to get user_id from form data, JSON, or URL parameter
    user_id = (request.form.get("user_id") or 
               (request.json.get("user_id") if request.is_json else None) or
               request.args.get("user_id"))
    
    if not user_id:
        return jsonify({"status": "error", "message": "User ID is required"}), 400
    
    # Convert to integer if it's a string
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID format"}), 400
    
    try:
        # Get the user to check for phone_number
        user = User.query.filter_by(id=user_id).first()
        
        # Get all responses for this user_id
        responses = Response.query.filter_by(user_id=user_id).all()
        
        # Delete associated files
        for response in responses:
            if response.file_path and not response.file_path.startswith('http'):
                try:
                    # Handle both absolute and relative paths
                    if os.path.isabs(response.file_path):
                        file_path = response.file_path
                    else:
                        # If it's a relative path, construct it from UPLOAD_FOLDER
                        file_path = os.path.join(current_app.config.get("UPLOAD_FOLDER", "_uploads"), 
                                               str(response.question_id), 
                                               os.path.basename(response.file_path))
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        current_app.logger.info(f'Deleted file: {file_path}')
                except Exception as e:
                    current_app.logger.warning(f"Could not delete file {response.file_path}: {e}")
        
        # Delete responses
        response_count = len(responses)
        for response in responses:
            db.session.delete(response)
        
        # Also delete progress entry if exists
        progress_entries = Progress.query.filter_by(user_id=user_id).all()
        for progress in progress_entries:
            db.session.delete(progress)
        
        db.session.commit()
        
        # Send WhatsApp notification if user has a phone number
        if user and user.phone_number:
            try:
                whatsapp_client = WhatsAppClient()
                deletion_message = "Your data has been deleted from our system. Thank you for your participation."
                message_response = whatsapp_client.send_text_message(user.phone_number, deletion_message)
                if whatsapp_client.is_message_sent_successfully(message_response):
                    current_app.logger.info(f'Sent deletion notification to {user.phone_number} for user {user_id}')
                else:
                    current_app.logger.warning(f'Failed to send deletion notification to {user.phone_number}: {message_response.status_code} - {message_response.text}')
            except Exception as e:
                # Don't fail the deletion if WhatsApp message fails
                current_app.logger.error(f'Error sending WhatsApp deletion notification: {e}', exc_info=True)
        
        current_app.logger.info(f'Deleted {response_count} responses for user_id {user_id}')
        return jsonify({"status": "success", "message": f"Deleted {response_count} responses"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting survey data: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to delete data"}), 500


@bp.route("/manage_data", methods=["GET", "POST"])
def manage_data():
    """Manage data page - allows users to delete their data using User ID and Token"""
    # Ensure session is initialized for CSRF token
    session.permanent = True
    
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        user_token = request.form.get("user_token", "").strip()
        
        if not user_id or not user_token:
            flash("Please provide both User ID and User Token.", "error")
            return render_template("manage_data.html")
        
        # Convert user_id to integer
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            flash("Invalid User ID format.", "error")
            return render_template("manage_data.html")
        
        try:
            # Find user by id and token
            user = User.query.filter_by(id=user_id, token=user_token).first()
            
            if not user:
                flash("Invalid User ID or User Token. Please check your information and try again.", "error")
                return render_template("manage_data.html")
            
            # Get all responses for this user_id
            responses = Response.query.filter_by(user_id=user_id).all()
            
            # Delete associated files
            for response in responses:
                if response.file_path and not response.file_path.startswith('http'):
                    try:
                        # Handle both absolute and relative paths
                        if os.path.isabs(response.file_path):
                            file_path = response.file_path
                        else:
                            # If it's a relative path, construct it from UPLOAD_FOLDER
                            file_path = os.path.join(current_app.config.get("UPLOAD_FOLDER", "_uploads"), 
                                                   str(response.question_id), 
                                                   os.path.basename(response.file_path))
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            current_app.logger.info(f'Deleted file: {file_path}')
                    except Exception as e:
                        current_app.logger.warning(f"Could not delete file {response.file_path}: {e}")
            
            # Delete progress entries
            progress_entries = Progress.query.filter_by(user_id=user_id).all()
            for progress in progress_entries:
                db.session.delete(progress)
            
            # Send WhatsApp notification if user has a phone number
            phone_number = user.phone_number
            if phone_number:
                try:
                    whatsapp_client = WhatsAppClient()
                    deletion_message = "Your data has been deleted from our system. Thank you for your participation."
                    message_response = whatsapp_client.send_text_message(phone_number, deletion_message)
                    if whatsapp_client.is_message_sent_successfully(message_response):
                        current_app.logger.info(f'Sent deletion notification to {phone_number} for user {user_id}')
                    else:
                        current_app.logger.warning(f'Failed to send deletion notification to {phone_number}: {message_response.status_code} - {message_response.text}')
                except Exception as e:
                    # Don't fail the deletion if WhatsApp message fails
                    current_app.logger.error(f'Error sending WhatsApp deletion notification: {e}', exc_info=True)
            
            # Delete the user (this will cascade delete responses due to cascade='all, delete-orphan')
            db.session.delete(user)
            db.session.commit()
            
            response_count = len(responses)
            current_app.logger.info(f'Deleted user {user_id} and {response_count} responses')
            # Render template with success flag instead of redirecting immediately
            return render_template("manage_data.html", deletion_success=True, response_count=response_count)
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting user data: {e}", exc_info=True)
            flash("An error occurred while deleting your data. Please try again.", "error")
            return render_template("manage_data.html")
    
    return render_template("manage_data.html")


@bp.route("/delete_all_data", methods=["POST"])
@login_required
def delete_all_data():
    """Delete all responses for the logged-in user"""
    try:
        # Get all user_ids associated with this account
        user_ids_to_delete = [current_user.id]
        
        # Add any user_ids from the user_ids field
        if current_user.user_ids:
            user_ids_list = [uid.strip() for uid in current_user.user_ids.split(",") if uid.strip()]
            for uid_str in user_ids_list:
                try:
                    uid_int = int(uid_str)
                    if uid_int not in user_ids_to_delete:
                        user_ids_to_delete.append(uid_int)
                except ValueError:
                    continue
        
        # Get all responses for this user
        responses = Response.query.filter(
            Response.user_id.in_(user_ids_to_delete)
        ).all()
        
        # Delete associated files
        for response in responses:
            if response.file_path and not response.file_path.startswith('http'):
                try:
                    # Handle both absolute and relative paths
                    if os.path.isabs(response.file_path):
                        file_path = response.file_path
                    else:
                        # If it's a relative path, construct it from UPLOAD_FOLDER
                        file_path = os.path.join(current_app.config.get("UPLOAD_FOLDER", "_uploads"), 
                                               str(response.question_id), 
                                               os.path.basename(response.file_path))
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        current_app.logger.info(f'Deleted file: {file_path}')
                except Exception as e:
                    current_app.logger.warning(f"Could not delete file {response.file_path}: {e}")
        
        # Delete responses
        response_count = len(responses)
        for response in responses:
            db.session.delete(response)
        
        # Delete progress entries
        progress_entries = Progress.query.filter(
            Progress.user_id.in_(user_ids_to_delete)
        ).all()
        for progress in progress_entries:
            db.session.delete(progress)
        
        # Clear user_ids field
        current_user.user_ids = None
        
        db.session.commit()
        
        # Send WhatsApp notification if user has a phone number
        if current_user.phone_number:
            try:
                whatsapp_client = WhatsAppClient()
                deletion_message = "Your data has been deleted from our system. Thank you for your participation."
                message_response = whatsapp_client.send_text_message(current_user.phone_number, deletion_message)
                if whatsapp_client.is_message_sent_successfully(message_response):
                    current_app.logger.info(f'Sent deletion notification to {current_user.phone_number} for user {current_user.id}')
                else:
                    current_app.logger.warning(f'Failed to send deletion notification to {current_user.phone_number}: {message_response.status_code} - {message_response.text}')
            except Exception as e:
                # Don't fail the deletion if WhatsApp message fails
                current_app.logger.error(f'Error sending WhatsApp deletion notification: {e}', exc_info=True)
        
        current_app.logger.info(f'Deleted all data for user {current_user.id}: {response_count} responses')
        return jsonify({"status": "success", "message": f"Deleted {response_count} responses"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting all data: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to delete data"}), 500


@bp.route("/survey", methods=["GET", "POST"])
def survey():
    user_id = request.args.get("user_id")
    survey_id = request.args.get("survey_id")
    
    if not user_id:
        # generate unique user_name using current timestamp
        user = User(token=create_new_user_token())
        db.session.add(user)
        db.session.commit()
        
        # Redirect with survey_id if provided
        if survey_id:
            return redirect(url_for("routes.survey", user_id=user.id, survey_id=survey_id))
        else:
            return redirect(url_for("routes.survey", user_id=user.id))

    # Get the user from database
    user = User.query.filter_by(id=user_id).first()
    if not user:
        return "User not found", 404

    # Get survey by ID if provided, otherwise use default
    if survey_id:
        try:
            survey_id = int(survey_id)
            survey = Survey.query.filter_by(id=survey_id).first()
        except (ValueError, TypeError):
            survey = None
    else:
        survey_name = os.getenv('DEFAULT_SURVEY', 'example_survey')
        survey = Survey.query.filter_by(name=survey_name).first()
    
    if not survey:
        error_msg = f'Survey not found for user {user_id}'
        if survey_id:
            error_msg += f' (survey_id: {survey_id})'
        current_app.logger.error(error_msg)
        return "Survey not found", 404

    # Get the current question based on the Progress
    progress = Progress.query.filter_by(user_id=user.id, survey_id=survey.id).first()
    if progress:
        # current_question_id is a foreign key to Question.id
        current_question = Question.query.filter_by(
            id=progress.current_question_id,
            survey_id=survey.id
        ).first() if progress.current_question_id else None
        current_question_group = current_question.question_group if current_question else None
        last_prompt_sent = current_question.prompt_number if current_question else None
    else:
        # create new progress entry - start with prompt_number 0
        current_question = Question.query.filter_by(
            survey_id=survey.id,
            prompt_number=0
        ).first()
        if not current_question:
            return "No questions found in survey", 404
            
        progress = Progress(
            user_id=user.id,
            survey_id=survey.id,
            current_question_id=current_question.id
        )
        db.session.add(progress)
        db.session.commit()
        
        current_question_group = current_question.question_group
        last_prompt_sent = current_question.prompt_number if current_question else 0

    if not current_question:
        if last_prompt_sent is not None and last_prompt_sent >= 0:
            current_app.logger.info(f'Completed survey for user {user_id}')
            user_token = user.token if user else None
            return render_template("survey_complete.html", current_user=current_user, user_id=user_id, user_token=user_token)
        else:
            current_app.logger.warning(f'No current question found for user {user_id} in survey {survey_name}')
            return "No current question found"
    
    if request.method == "POST":
        try:
            # Update user_ids if user is logged in
            update_user_ids_if_logged_in(user_id)
            
            # Check if this is a skip request for random group
            if request.form.get("skip_question") == "true":
                if current_question_group and current_question_group.group_type == "random":
                    # Get another random question from the group
                    group_questions = Question.query.filter_by(
                        question_group_id=current_question_group.id
                    ).all()
                    if group_questions:
                        # Exclude the current question to get a different one
                        available_questions = [q for q in group_questions if q.id != current_question.id]
                        if not available_questions:
                            available_questions = group_questions  # If only one question, use it
                        selected_question = random.choice(available_questions)
                        progress.current_question_id = selected_question.id
                        db.session.commit()
                        return redirect(url_for("routes.survey", user_id=user_id))
                return redirect(url_for("routes.survey", user_id=user_id))
            
            # Extract question IDs from form data
            # Form fields are named like: question_{id}_selected_option, question_{id}_response_text, etc.
            question_ids = set()
            for key in request.form.keys():
                if key.startswith('question_'):
                    parts = key.split('_')
                    if len(parts) >= 2:
                        try:
                            q_id = int(parts[1])
                            question_ids.add(q_id)
                        except ValueError:
                            continue
            
            # Also check file uploads for audio questions
            for key in request.files.keys():
                if key.startswith('question_'):
                    parts = key.split('_')
                    if len(parts) >= 2:
                        try:
                            q_id = int(parts[1])
                            question_ids.add(q_id)
                        except ValueError:
                            continue
            
            # If no question IDs found, fall back to current question
            if not question_ids:
                question_ids = {current_question.id} if current_question else set()
            
            # Get all questions that need responses
            questions_to_process = Question.query.filter(
                Question.id.in_(question_ids),
                Question.survey_id == survey.id
            ).all()
            
            if not questions_to_process:
                current_app.logger.warning(f'No questions found for IDs: {question_ids}')
                return jsonify({"status": "error", "message": "No questions found"}), 400
            
            # Process responses for all questions
            # For select groups, questions are optional, so we only create responses for answered questions
            responses_created = []
            last_response = None
            last_question = None
            
            for question in questions_to_process:
                response = _create_web_response(user, question, request)
                if response:
                    db.session.add(response)
                    responses_created.append(response)
                    last_response = response
                    last_question = question
                    current_app.logger.info(f'Committed Response for question {question.id}: {response}')
                else:
                    # It's okay if an optional question has no response
                    if not question.required:
                        current_app.logger.debug(f'No response provided for optional question {question.id}')
            
            if responses_created:
                db.session.commit()
            else:
                # Check if all questions are optional - if so, allow submission
                all_optional = all(not q.required for q in questions_to_process)
                if all_optional:
                    current_app.logger.info(f'No responses provided, but all questions are optional - allowing submission')
                    db.session.commit()
            
            # Determine next prompt number using survey logic from the last response
            if last_response and last_question:
                next_prompt_number = _handle_web_survey_logic(
                    survey_name, 
                    last_question, 
                    last_response.response_value, 
                    last_response.response_type
                )
            else:
                # Default: move to next question
                next_prompt_number = (current_question.prompt_number + 1) if current_question else None

            # Update user's progress
            if next_prompt_number is not None:
                # Find the question with this prompt_number
                next_question = Question.query.filter_by(
                    survey_id=survey.id,
                    prompt_number=next_prompt_number
                ).first()
                if next_question:
                    progress.current_question_id = next_question.id
                else:
                    # If no question found, survey is complete
                    progress.current_question_id = None
                    db.session.commit()
                    user_token = user.token if user else None
                    return render_template("survey_complete.html", current_user=current_user, user_id=user_id, user_token=user_token)
            else:
                # Survey completed
                progress.current_question_id = None
            
            db.session.commit()
            current_app.logger.info(f'Updated user progress to question {progress.current_question_id}')
            
            # Check if survey is complete
            if progress.current_question_id is None:
                user_token = user.token if user else None
                return render_template("survey_complete.html", current_user=current_user, user_id=user_id, user_token=user_token)
            
            return redirect(url_for("routes.survey", user_id=user_id))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error processing response: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
    
    
    if current_question_group:
        current_app.logger.debug(f'Current question group: {current_question_group.name}, type: {current_question_group.group_type}')

        if current_question_group.group_type == "random":
            # Fetch all questions in the group
            group_questions = Question.query.filter_by(
                question_group_id=current_question_group.id
            ).all()
            if not group_questions:
                return "No questions found in group", 404
            selected_question = random.choice(group_questions)
            current_app.logger.debug(f'Randomly selected question {selected_question.id} from group {current_question_group.name}')
            questions_data = [
                {
                    "id": selected_question.id,
                    "prompt": selected_question.prompt,
                    "question_type": selected_question.question_type,
                    "options": selected_question.options,
                    "required": selected_question.required
                }
            ]

            return render_template("survey.html", 
                                   questions=questions_data,
                                   group_type="random",
                                   question_group_id=current_question_group.id,
                                   current_user=current_user,
                                   user_id=user_id)
        
        elif current_question_group.group_type == "select":
            # prompt the user with all the questions in the group to select from and answer
            group_questions = Question.query.filter_by(
                question_group_id=current_question_group.id
            ).all()
            if not group_questions:
                return "No questions found in group", 404
            questions_data = [
                {
                    "id": q.id,
                    "prompt": q.prompt,
                    "question_type": q.question_type,
                    "options": q.options,
                    "required": q.required
                } for q in group_questions
            ]
            return render_template("survey.html", 
                                   questions=questions_data,
                                   group_type="select",
                                   current_user=current_user,
                                   user_id=user_id)
        
        else:
            # Sequential group - just show the current question
            pass

    questions_data = [
        {
            "id": current_question.id,
            "prompt": current_question.prompt,
            "question_type": current_question.question_type,
            "options": current_question.options,
            "required": current_question.required
        }
    ]
    
    return render_template("survey.html", 
                        questions=questions_data,
                        group_type=None,
                        current_user=current_user,
                        user_id=user_id)
    
@bp.route("/submit_audio", methods=["POST"])
def submit_audio():
    """Handle audio file submission"""
    question_id = request.form.get("question_id")
    survey_id = request.form.get("survey_id")
    audio = request.files.get("audio")
    
    # Check if this is an AJAX request
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
              'application/json' in request.headers.get('Accept', '')

    if not audio:
        if is_ajax:
            return jsonify({"status": "error", "message": "No audio file provided"}), 400
        else:
            flash("يرجى اختيار ملف صوتي.", "error")
            return redirect(url_for('routes.record', question_id=question_id, survey_id=survey_id))

    # Handle case where filename might be None or empty (e.g., from Blob)
    filename = audio.filename
    if not filename:
        # Generate a default filename with webm extension if none provided
        filename = f"recording_{int(time.time())}.webm"
        current_app.logger.warning("Audio file submitted without filename, using default")
        # Note: We can't modify audio.filename directly, but save_audio_file will handle None
    
    # Check file extension (use .webm as default if filename is None)
    if not allowed_file(filename or "recording.webm"):
        if is_ajax:
            return jsonify({"status": "error", "message": f"Invalid file type. Allowed: {current_app.config['ALLOWED_EXTENSIONS']}"}), 400
        else:
            flash("نوع الملف غير مدعوم.", "error")
            return redirect(url_for('routes.record', question_id=question_id, survey_id=survey_id))

    try:
        # Get user from session
        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            user_id = session.get("user_id")
            if user_id:
                try:
                    user_id = int(user_id)
                    user = User.query.filter_by(id=user_id).first()
                except (ValueError, TypeError):
                    pass
        
        if not user:
            if is_ajax:
                return jsonify({"status": "error", "message": "User not found"}), 400
            else:
                flash("يرجى البدء من الصفحة الرئيسية.", "error")
                return redirect(url_for('routes.index'))
        
        if not question_id:
            if is_ajax:
                return jsonify({"status": "error", "message": "Question ID required"}), 400
            else:
                flash("معرف السؤال مطلوب.", "error")
                return redirect(url_for('routes.select_theme'))
        
        try:
            question_id = int(question_id)
        except (ValueError, TypeError):
            if is_ajax:
                return jsonify({"status": "error", "message": "Invalid question ID"}), 400
            else:
                flash("معرف السؤال غير صحيح.", "error")
                return redirect(url_for('routes.select_theme'))
        
        filename = save_audio_file(audio, str(user.id), str(question_id))
        
        # Create response
        import os
        response = Response(
            user_id=user.id,
            question_id=question_id,
            response_type="audio",
            file_path=os.path.join(current_app.config["UPLOAD_FOLDER"], str(question_id), filename)
        )
        db.session.add(response)
        db.session.commit()
        
        current_app.logger.info(f'Audio submitted by user {user.id} for question {question_id}')
        
        # Redirect to thanks page (for form submissions) or return JSON (for AJAX)
        if is_ajax:
            return jsonify({"status": "success", "file": filename, "redirect": url_for('routes.thanks')})
        else:
            return redirect(url_for('routes.thanks'))
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Upload failed: {e}", exc_info=True)
        if is_ajax:
            return jsonify({"status": "error", "message": "Upload failed"}), 500
        else:
            flash("حدث خطأ أثناء رفع الملف. يرجى المحاولة مرة أخرى.", "error")
            return redirect(url_for('routes.record', question_id=question_id, survey_id=survey_id))


@bp.route("/whatsapp-webhook-endpoint", methods=["GET", "POST"])
@csrf.exempt
def whatsapp_webhook_endpoint():
    """
    WhatsApp webhook endpoint
    """
    # Handle webhook verification (GET request)
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        challenge_value = request.args.get("hub.challenge")
        token = request.args.get("hub.verify_token")

        if mode == "subscribe" and token == os.getenv('WHATSAPP_VERIFY_TOKEN'):
            return challenge_value
        else:
            return "Invalid token", 403

    # Handle incoming messages (POST request)
    if request.method == "POST":
        current_app.logger.info(f"Received WhatsApp webhook request from {request.remote_addr}")

        ## Process incoming request payload
        # Handle status notifications (sent, delivered, read acknowledgements)
        try:
            message_metadata = request.json["entry"][0]["changes"][0]["value"]["statuses"]
            current_app.logger.info("Ignored status notification")
            return "OK"
        except KeyError:
            pass
        # parse message
        current_app.logger.info('Processing WhatsApp message')
        message_metadata = request.json["entry"][0]["changes"][0]["value"]["messages"]
        current_app.logger.info(f'Message metadata: {message_metadata}')
        parsed_message = _parse_whatsapp_message(message_metadata)
        # Get or create user
        user = User.query.filter_by(phone_number=parsed_message["from_field"]).first()
        if not user:
            current_app.logger.info(f'Creating new WhatsApp user for phone: {parsed_message["from_field"]}')
            user = User(
                phone_number=parsed_message["from_field"],
                token=create_new_user_token(),
                survey_name=os.getenv('WHATSAPP_DEFAULT_SURVEY', 'example_survey'),
                last_prompt_sent=None
            )
            db.session.add(user)
            db.session.commit()
        # Assign survey to user, if necessary
        survey_name = user.survey_name or os.getenv('WHATSAPP_DEFAULT_SURVEY', 'example_survey')
        survey = Survey.query.filter_by(name=survey_name).first()
        if not survey:
            current_app.logger.error(f'Survey {survey_name} not found')
            return "Survey not found", 404
        survey_id = survey.id


        # Handle input from user.
        # The expected workflow is as follows:
        # - The user texts the server. This creates the user in the database and initializes
        #   last_prompt_sent=None and demographics_and_consent_completed=False.
        # - The user is sent a series of consent and demographic questions. After this, last_prompt_sent=0.
        # -- The demographic/consent questions are as follows:
        # -- 0. Participant Information Sheet agreement
        # -- 1. Citizenship question
        # -- 2. Age group question
        # -- 3. Birth region question
        # -- 4. Residence question
        # -- 5. Optional inputs question
        # -- 6. Consent question 1
        # -- 7. Consent question 2
        # -- 8. Consent question 3
        # - When the demographic and consent section has been completed, demographic_and_consent_completed=True
        # - The user is sent the survey questions, with each type of question being handled by a different code block below.
        #   When the first question is sent, last_prompt_sent=0.
        #   After each question, last_prompt_sent is incremented by one.
        # - When there are no more questions, the survey completed message is sent.

        # This condition means that the user is in the demographic/consent workflow and hasn't begun the survey yet.
        if not user.demographics_and_consent_completed:

            # The user has not answered "Yes" to the very first question of the demographic/consent workflow yet.
            if not user.consent_read_form:
                # This is probably the initial interaction:
                # - the user is not repsonding to a structured prompt (message_type!=interactive), which the very first question is,
                # - they're still in the demographic/consent phase (because last_prompt_sent=None), and
                # - they haven't answered "Yes" to the very first question (user.consent_read_form)
                if parsed_message.get("message_type") != "interactive":
                    # Send first message of demographic/consent workflow
                    whatsapp_client = WhatsAppClient()
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
                        return "OK"
                    else:
                        current_app.logger.error(f"Failed to send consent question: {message_response.status_code} - {message_response.text}")
                        return "Failed to send consent question", 500

                # This should be the user's response to the first question.
                elif parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "button_reply":
                    button_id = parsed_message.get("interactive_field_reply_button_id")
                    # This is the user acknowledging that they've read the Participant Information Sheet and agree to it.
                    if button_id == "consent_yes":
                        # User accepted terms - set consent_read_form to True
                        user.consent_read_form = True
                        db.session.commit()
                        current_app.logger.info(f'User {parsed_message["from_field"]} accepted terms and conditions')
                        
                        # Send next question: Emirati Citizenship
                        whatsapp_client = WhatsAppClient()
                        citizenship_question = "Are you an Emirati citizen?"
                        buttons = [
                            {"type": "reply", "reply": {"id": "citizenship_yes", "title": "Yes"}},
                            {"type": "reply", "reply": {"id": "citizenship_no", "title": "No"}}
                        ]
                        message_response = whatsapp_client.send_button_message(
                            parsed_message["from_field"],
                            citizenship_question,
                            buttons
                        )
                        if whatsapp_client.is_message_sent_successfully(message_response):
                            current_app.logger.info(f'Sent citizenship question to {parsed_message["from_field"]}')
                            return "OK"
                        else:
                            current_app.logger.error(f"Failed to send citizenship question: {message_response.status_code} - {message_response.text}")
                            return "Failed to send question", 500
                    elif button_id == "consent_no":
                        # User declined terms
                        whatsapp_client = WhatsAppClient()
                        decline_message = "Thank you for your interest. Unfortunately, we cannot proceed without your acceptance of the terms and conditions."
                        message_response = whatsapp_client.send_text_message(parsed_message["from_field"], decline_message)
                        if whatsapp_client.is_message_sent_successfully(message_response):
                            current_app.logger.info(f'User {parsed_message["from_field"]} declined terms and conditions')
                            return "OK"
                        else:
                            current_app.logger.error(f"Failed to send decline message: {message_response.status_code} - {message_response.text}")
                            return "Failed to send message", 500
                    else:
                        raise ValueError("The only expected values are \"consent_yes\" and \"consent_no\".")

                else:
                    raise ValueError("There shouldn't be a non-button-reply interactive message from the user when last_prompt_sent==0 and user.consent_read_form=False.")

            # This means the user has passed the first demographic/consent question, but not the second.
            # See above for the text of the second question.
            elif user.consent_read_form and user.emirati_citizenship==None:
                if parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "button_reply":
                    button_id = parsed_message.get("interactive_field_reply_button_id")
                    if button_id in ["citizenship_yes", "citizenship_no"]:
                        # Handle citizenship response
                        user.emirati_citizenship = (button_id == "citizenship_yes")
                        db.session.commit()
                        current_app.logger.info(f'User {parsed_message["from_field"]} answered citizenship: {user.emirati_citizenship}')
                        
                        # Send Age Group question (use list message for more than 3 options)
                        age_question = "What is your age group?"
                        list_items = [
                            {"id": "age_1", "title": "18 to 25 years"},
                            {"id": "age_2", "title": "26 to 35 years"},
                            {"id": "age_3", "title": "36 to 45 years"},
                            {"id": "age_4", "title": "46 to 55 years"},
                            {"id": "age_5", "title": "56 to 65 years"},
                            {"id": "age_6", "title": "65 years and above"}
                        ]
                        sections = [{
                            "title": "Select age group",
                            "rows": list_items
                        }]
                        whatsapp_client = WhatsAppClient()
                        message_response = whatsapp_client.send_list_message(
                            parsed_message["from_field"],
                            age_question,
                            "Select Age Group",
                            sections
                        )
                        if whatsapp_client.is_message_sent_successfully(message_response):
                            return "OK"
                        return "Failed to send age question", 500
                    else:
                        raise ValueError("The only expected values in this context are \"citizenship_yes\" and \"citizenship_no\".")
                else:
                    raise ValueError("The only expected type of response here is an \"interactive\" \"button_reply\".")

            # This means the user has passed the second demographic/consent question, but not the third.
            # See above for the text of the third question.
            elif user.emirati_citizenship!=None and user.age_group==None:
                if parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "list_reply":
                    list_id = parsed_message.get("interactive_field_list_id")
                    if list_id and list_id.startswith("age_"):
                        age_value = int(list_id.split("_")[1])
                        user.age_group = age_value
                        db.session.commit()
                        current_app.logger.info(f'User {parsed_message["from_field"]} answered age group: {age_value}')
                        
                        # Send Place of Birth question (use list message)
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
                        sections = [{
                            "title": "Select Emirate",
                            "rows": list_items
                        }]
                        whatsapp_client = WhatsAppClient()
                        message_response = whatsapp_client.send_list_message(
                            parsed_message["from_field"],
                            place_question,
                            "Select Birthplace",
                            sections
                        )
                        if whatsapp_client.is_message_sent_successfully(message_response):
                            return "OK"
                        return "Failed to send place of birth question", 500
                    else:
                        raise ValueError("All valid responses will have values of the form \"age_*\".")
                else:
                    raise ValueError(f"The only expected type of response here is an \"interactive\" \"list_reply\". Instead got: {parsed_message}")

            # This means the user has passed the third demographic/consent question, but not the fourth.
            # See above for the text of the fourth question.
            # This question begins with the user choosing an option from a list and, if the chosen option is "Other",
            # then being send a text message, expecting a text response. If the user doesn't choose "Other" from
            # the list, then their chosen option is written to the database. Otherwise, whatever they input as a
            # response to the text message is written to the database.
            elif user.age_group!=None and user.place_of_birth==None:
                # This is the user's response chosen from the list.
                if parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "list_reply":
                    list_id = parsed_message.get("interactive_field_list_id")
                    if list_id and list_id.startswith("place_"):
                        place_value = list_id.split("_",1)[1]
                        if place_value != "other":
                            user.place_of_birth = place_value
                            db.session.commit()
                            current_app.logger.info(f'User {parsed_message["from_field"]} answered place of birth: {place_value}')

                            # Send Current Residence question (use list message)
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
                            sections = [{
                                "title": "Select Emirate",
                                "rows": list_items
                            }]
                            whatsapp_client = WhatsAppClient()
                            message_response = whatsapp_client.send_list_message(
                                parsed_message["from_field"],
                                residence_question,
                                "Select Residence",
                                sections
                            )
                            if whatsapp_client.is_message_sent_successfully(message_response):
                                return "OK"
                            return "Failed to send residence question", 500

                        elif place_value == "other":
                            other_question = "Please specify your place of birth."
                            whatsapp_client = WhatsAppClient()
                            message_response = whatsapp_client.send_text_message(parsed_message["from_field"], other_question)
                            if whatsapp_client.is_message_sent_successfully(message_response):
                                return "OK"
                            return "Failed to send residence question", 500

                    else:
                        raise ValueError("All valid responses will have values of the form \"place_*\".")
                # This is the user's response to the open-ended text prompt, if they previously chose the "Other" option.
                elif parsed_message.get("message_type") == "text":
                    place_value = parsed_message.get("text_field")
                    user.place_of_birth = place_value
                    db.session.commit()
                    current_app.logger.info(f'User {parsed_message["from_field"]} answered place of birth: {place_value}')

                    # Send Current Residence question (use list message)
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
                    sections = [{
                        "title": "Select Emirate",
                        "rows": list_items
                    }]
                    whatsapp_client = WhatsAppClient()
                    message_response = whatsapp_client.send_list_message(
                        parsed_message["from_field"],
                        residence_question,
                        "Select Residence",
                        sections
                    )
                    if whatsapp_client.is_message_sent_successfully(message_response):
                        return "OK"
                    return "Failed to send residence question", 500

                else:
                    raise ValueError("The only expected types of responses here are \"interactive\" \"list_reply\" and \"text\".")

            # This means the user has passed the fourth demographic/consent question, but not the fifth.
            # See above for the text of the fifth question.
            # This question begins with the user choosing an option from a list and, if the chosen option is "Other",
            # then being send a text message, expecting a text response. If the user doesn't choose "Other" from
            # the list, then their chosen option is written to the database. Otherwise, whatever they input as a
            # response to the text message is written to the database.
            elif user.place_of_birth!=None and user.current_residence==None:
                # This is the user's response chosen from the list.
                if parsed_message.get("message_type") == "interactive" and parsed_message.get("interactive_field_type") == "list_reply":
                    list_id = parsed_message.get("interactive_field_list_id")
                    if list_id and list_id.startswith("residence_"):
                        place_value = list_id.split("_",1)[1]
                        if place_value != "other":
                            user.current_residence = place_value
                            db.session.commit()
                            current_app.logger.info(f'User {parsed_message["from_field"]} answered place of birth: {place_value}')

                            # Send Optional Name and Contact question
                            optional_question = "[Optional] Name:\n[Optional] Contact number:\n\nNote: Your name and contact number, if provided, will be stored with your data until August 31, 2027. After that, this information will be permanently deleted, and you won't be able to access your specific data by request.\n\nYou can reply with:\n- Just your name\n- Just your contact number\n- Both (name and contact on separate lines)\n- Or simply send \"No\" to skip this question."
                            whatsapp_client = WhatsAppClient()
                            message_response = whatsapp_client.send_text_message(parsed_message["from_field"], optional_question)
                            if whatsapp_client.is_message_sent_successfully(message_response):
                                return "OK"
                            return "Failed to send optional info request", 500

                        elif place_value == "other":
                            other_question = "Please specify your place of birth."
                            whatsapp_client = WhatsAppClient()
                            message_response = whatsapp_client.send_text_message(parsed_message["from_field"], other_question)
                            if whatsapp_client.is_message_sent_successfully(message_response):
                                return "OK"
                            return "Failed to send residence question", 500

                    else:
                        raise ValueError("All valid responses will have values of the form \"residence_*\".")
                # This is the user's response to the open-ended text prompt, if they previously chose the "Other" option.
                elif parsed_message.get("message_type") == "text":
                    place_value = parsed_message.get("text_field")
                    user.current_residence = place_value
                    db.session.commit()
                    current_app.logger.info(f'User {parsed_message["from_field"]} answered place of birth: {place_value}')

                    # Send Optional Name and Contact question
                    optional_question = "[Optional] Name:\n[Optional] Contact number:\n\nNote: Your name and contact number, if provided, will be stored with your data until August 31, 2027. After that, this information will be permanently deleted, and you won't be able to access your specific data by request.\n\nYou can reply with:\n- Just your name\n- Just your contact number\n- Both (name and contact on separate lines)\n- Or simply send \"No\" to skip this question."
                    whatsapp_client = WhatsAppClient()
                    message_response = whatsapp_client.send_text_message(parsed_message["from_field"], optional_question)
                    if whatsapp_client.is_message_sent_successfully(message_response):
                        return "OK"
                    return "Failed to send optional info request", 500

                else:
                    raise ValueError("The only expected types of responses here are \"interactive\" \"list_reply\" and \"text\".")

            # This means the user has passed the fifth demographic/consent question, but not the sixth.
            # See above for the text of the sixth question.
            elif user.current_residence!=None and user.real_name_optional_input==None and user.phone_number_optional_input==None:
                # This is the user's response to the open-ended text prompt, if they previously chose the "Other" option.
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
                    #current_app.logger.info(f'User {parsed_message["from_field"]} answered name: {name_value} and phone number: {number_value}')
                    current_app.logger.info(f'User {parsed_message["from_field"]} answered with optional information')

                    # Send first consent question
                    consent_question_1 = "I agree to the use of my data for research and development purposes (including the extraction of linguistic features for building the dictionary and training AI models)."
                    buttons = [
                        {"type": "reply", "reply": {"id": "consent_required_yes", "title": "Yes"}},
                        {"type": "reply", "reply": {"id": "consent_required_no", "title": "No"}}
                    ]
                    whatsapp_client = WhatsAppClient()
                    message_response = whatsapp_client.send_button_message(
                        parsed_message["from_field"],
                        consent_question_1,
                        buttons
                    )
                    if whatsapp_client.is_message_sent_successfully(message_response):
                        return "OK"
                    return "Failed to send consent question 1", 500
                else:
                    raise ValueError("The only expected type of response here is \"text\".")

            # This means the user has passed the sixth demographic/consent question, but not the seventh.
            # See above for the text of the seventh question.
            elif (user.real_name_optional_input!=None or user.phone_number_optional_input!=None) and user.consent_required==None:
                if parsed_message.get("interactive_field_type") == "button_reply":
                    button_id = parsed_message.get("interactive_field_reply_button_id")
                    if button_id in ["consent_required_yes", "consent_required_no"]:
                        if button_id == "consent_required_yes":
                            user.consent_required = True
                        else:
                            user.consent_required = False
                        db.session.commit()
                        current_app.logger.info(f'User {parsed_message["from_field"]} answered consent question 1: {button_id == "consent_required_yes"}')
                        
                        # Send second consent question (always asked)
                        consent_question_2 = "[Optional] I agree to the archiving and sharing of my audio recordings with researchers and/or their release on public platforms."
                        buttons = [
                            {"type": "reply", "reply": {"id": "consent_optional_yes", "title": "Yes"}},
                            {"type": "reply", "reply": {"id": "consent_optional_no", "title": "No"}}
                        ]
                        whatsapp_client = WhatsAppClient()
                        message_response = whatsapp_client.send_button_message(
                            parsed_message["from_field"],
                            consent_question_2,
                            buttons
                        )
                        if whatsapp_client.is_message_sent_successfully(message_response):
                            return "OK"
                        return "Failed to send consent question 2", 500
                    else:
                        raise ValueError("All valid responses will have values of \"consent_required_yes\" or \"consent_required_no\".")
                else:
                    raise ValueError("The only expected type of response here is an \"interactive\" \"button reply\".")

            # This means the user has passed the seventh demographic/consent question, but not the eighth.
            # See above for the text of the eighth question.
            elif user.consent_required!=None and user.consent_optional==None:
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
                            current_app.logger.info(f'User {parsed_message["from_field"]} finished the onboadring process!')
                            # Send confirmation that onboarding is finished
                            completion_message = "Thank you! You have finished the onboarding process.\n\nWhenever you are ready to begin the survey, respond with any message."
                            whatsapp_client = WhatsAppClient()
                            message_response = whatsapp_client.send_text_message(parsed_message["from_field"], completion_message)
                            if whatsapp_client.is_message_sent_successfully(message_response):
                                return "OK"
                            return "Failed to send completion message", 500

                        elif button_id == "consent_optional_no":
                            # Send third consent question
                            consent_question_3 = "I agree to the archiving the text transcripts derived from my audio recordings and sharing them with researchers and/or public platforms (with the audio itself not being shared)."
                            buttons = [
                                {"type": "reply", "reply": {"id": "consent_optional_alt_yes", "title": "Yes"}},
                                {"type": "reply", "reply": {"id": "consent_optional_alt_no", "title": "No"}}
                            ]
                            whatsapp_client = WhatsAppClient()
                            message_response = whatsapp_client.send_button_message(
                                parsed_message["from_field"],
                                consent_question_3,
                                buttons
                            )
                            if whatsapp_client.is_message_sent_successfully(message_response):
                                return "OK"
                            return "Failed to send consent question 3", 500
                    else:
                        raise ValueError("All valid responses will have values of \"consent_optional_yes\" or \"consent_optional_no\".")
                else:
                    raise ValueError("The only expected type of response here is an \"interactive\" \"button reply\".")

            # This means the user has passed the eighth demographic/consent question, but not the ninth.
            # See above for the text of the ninth question.
            elif user.consent_optional!=None and user.consent_optional_alternative==None:
                if parsed_message.get("interactive_field_type") == "button_reply":
                    button_id = parsed_message.get("interactive_field_reply_button_id")
                    if button_id in ["consent_optional_alt_yes", "consent_optional_alt_no"]:
                        if button_id == "consent_optional_alt_yes":
                            user.consent_optional_alternative = True
                        else:
                            user.consent_optional_alternative = False
                        user.demographics_and_consent_completed = True
                        db.session.commit()
                        current_app.logger.info(f'User {parsed_message["from_field"]} answered consent question 3: {button_id == "consent_optional_alt_yes"}')
                        current_app.logger.info(f'User {parsed_message["from_field"]} finished the onboadring process!')
                        # Send confirmation that onboarding is finished
                        completion_message = "Thank you! You have finished the onboarding process.\n\nWhenever you are ready to being the survey, respond with any message."
                        whatsapp_client = WhatsAppClient()
                        message_response = whatsapp_client.send_text_message(parsed_message["from_field"], completion_message)
                        if whatsapp_client.is_message_sent_successfully(message_response):
                            return "OK"
                        return "Failed to send completion message", 500
                    else:
                        raise ValueError(
                                f"All valid responses will have values of \"consent_optional_alt_yes\" or \"consent_optional_alt_no\".\n" +
                                f"Instead, we have button_id = {button_id}."
                        )
                else:
                    raise ValueError("The only expected type of response here is an \"interactive\" \"button reply\".")

            else:
                raise ValueError("Check this user's database state. Something's wrong.")

        # This means the user has already completed the demographic and consent questionnaire.
        # This is where the actual survey begins.
        else:
            if user.last_prompt_sent==None:
                new_prompt_number = 0
            else:
                new_prompt_number = user.last_prompt_sent + 1

                # Record response from user.

                # Get the current question based on last_prompt_sent
                current_question = Question.query.filter_by(
                    survey_id=survey_id,
                    prompt_number=user.last_prompt_sent
                ).first()
                if not current_question:
                    current_app.logger.warning(f'No question found for prompt {last_prompt_sent} in survey {survey_name}')
                    return "No current question found"

                # Check if we're handling a question from a "select" group. If so, which response are we looking at?
                current_question_group = current_question.question_group if current_question.question_group_id else None
                question_is_from_select_group = current_question_group and current_question_group.group_type == "select"
                response_is_list_selection = parsed_message.get("interactive_field_type") == "list_reply" and question_is_from_select_group

                # If the response is the user's question selection from the list of
                # questions in the "group_type: "select" group, find and send that
                # question.
                if question_is_from_select_group and response_is_list_selection:
                    selected_question_id = parsed_message.get("interactive_field_list_id")
                    try:
                        selected_question_id = int(selected_question_id)
                    except (ValueError, TypeError):
                        current_app.logger.error(f'Invalid question ID in list selection: {selected_question_id}')
                        return "Invalid selection", 400

                    ### TODO
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
                        # Set last_question_asked to the selected question's ID
                        user.last_question_asked = current_question.id
                        db.session.commit()
                        current_app.logger.info(f'Sent selected question {current_question.id} to {parsed_message["from_field"]}')
                        # Don't update last_prompt_sent - we're still at the same prompt_number, just showing a different question
                        return "OK"
                    else:
                        current_app.logger.error(f"Failed to send selected question: {message_response.status_code} - {message_response.text}")
                        return "Failed to send question", 500

                # This could be an audio response to a "group_type": "select"
                # question, or an audio response to any other type of question.
                else:
                    # Process media messages. Should be relevant, since all responses should be "audio"
                    if parsed_message['message_type'] in ["document", "sticker", "audio", "image", "video"]:
                        media_handler = WhatsAppMediaHandler(downloads_directory=os.path.join(current_app.config["UPLOAD_FOLDER"], str(current_question.id)))
                        message_media_metadata = media_handler.process_media(message_metadata)
                        parsed_message['media_download_location'] = message_media_metadata.get("media_download_location", "none")
                        parsed_message['message_media_metadata'] = message_media_metadata
                    # Check for audio requirement
                    if current_question.question_type == "audio" and parsed_message["media_download_location"] == "none":
                        current_app.logger.info(f'No audio file provided for question {current_question.id}')
                        return "No audio file provided"
                    # Create response based on message type
                    response = _create_whatsapp_response_from_message(
                        user, current_question, parsed_message
                    )
                    if response:
                        db.session.add(response)
                        db.session.commit()
                        current_app.logger.info(f'committed Response: {response}')


            # Handle sending out the next question.

            # Get the next question, if it exists.
            next_question = Question.query.filter_by(
                survey_id=survey_id,
                prompt_number=new_prompt_number
            ).first()

            # If the previous question was the last question, send a generic completion message.
            if not next_question:
                current_app.logger.info(f'Survey completed for user {parsed_message["from_field"]}. No more questions.')
                # Update user's last_prompt_sent
                user.last_prompt_sent = new_prompt_number
                db.session.commit()
                # Send completion message to user
                whatsapp_client = WhatsAppClient()
                completion_message = f"Survey completed! Thank you for your responses.\n\n" + \
                        f"If you'd like to delete your data later, please kaizoderp.com/manage_data and enter the following information."
                message_response = whatsapp_client.send_text_message(parsed_message["from_field"], completion_message)
                if whatsapp_client.is_message_sent_successfully(message_response):
                    current_app.logger.info(f'Sent completion message 1 of 3 to {parsed_message["from_field"]}')
                else:
                    current_app.logger.error(f"Failed to send completion message 1 of 3 to {parsed_message['from_field']}: {message_response.status_code} - {message_response.text}")
                completion_message = f"User ID: {user.id}"
                message_response = whatsapp_client.send_text_message(parsed_message["from_field"], completion_message)
                if whatsapp_client.is_message_sent_successfully(message_response):
                    current_app.logger.info(f'Sent completion message 2 of 3 to {parsed_message["from_field"]}')
                else:
                    current_app.logger.error(f"Failed to send completion message 2 of 3 to {parsed_message['from_field']}: {message_response.status_code} - {message_response.text}")
                completion_message = f"User Token: {user.token}"
                message_response = whatsapp_client.send_text_message(parsed_message["from_field"], completion_message)
                if whatsapp_client.is_message_sent_successfully(message_response):
                    current_app.logger.info(f'Sent completion message 3 of 3 to {parsed_message["from_field"]}')
                else:
                    current_app.logger.error(f"Failed to send completion message 3 of 3 to {parsed_message['from_field']}: {message_response.status_code} - {message_response.text}")
                return "Survey completed!"

            # Otherwise, we still have a question to send.
            else:
                # Check if next_question belongs to a "group_type": "select" or "group_type": "random" group
                next_question_group = next_question.question_group if next_question.question_group_id else None
                next_question_is_from_select_group = next_question_group and next_question_group.group_type == "select"
                next_question_is_from_random_group = next_question_group and next_question_group.group_type == "random"

                if next_question_is_from_select_group:
                    # Get all questions in the group
                    group_questions = Question.query.filter_by(
                        question_group_id=next_question_group.id
                    ).all()

                    if not group_questions:
                        current_app.logger.error(f'No questions found in select group {next_question_group.id}')
                        return "Next question is from select group, but the group contains no questions.", 404

                    # Create a list message with all questions
                    whatsapp_client = WhatsAppClient()
                    list_items = []
                    for q in group_questions:
                        # Use question ID as the list item ID so we can identify which question was selected
                        # Truncate title to 24 chars (WhatsApp limit) and use description for rest
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
                        parsed_message["from_field"],
                        body_text,
                        "Select Question",
                        sections
                    )
                    # Update user's last_prompt_sent if message was sent successfully
                    if whatsapp_client.is_message_sent_successfully(message_response):
                        user.last_prompt_sent = new_prompt_number
                        # Set last_question_asked to None when sending the selection list
                        user.last_question_asked = None
                        db.session.commit()
                        current_app.logger.info(f'Sent question selection list to {parsed_message["from_field"]} for prompt {new_prompt_number}')
                        return "OK"
                    else:
                        current_app.logger.error(f"Failed to send selection list: {message_response.status_code} - {message_response.text}")
                        return "Failed to send selection list", 500

                elif next_question_is_from_random_group:
                    random_next_question = Question.query.filter_by(
                        survey_id=survey_id,
                        prompt_number=new_prompt_number
                    ).order_by(func.random()).first()
                    # Send next question via WhatsApp
                    question_data = {
                        "question_type": random_next_question.question_type,
                        "text": random_next_question.prompt,
                        "options": random_next_question.options or {}
                    }
                    current_app.logger.info(f'Sending question data: {question_data}')
                    whatsapp_client = WhatsAppClient()
                    message_response = whatsapp_client.send_question_message(parsed_message["from_field"], question_data)
                    # Update user's last_prompt_sent if message was sent successfully
                    if whatsapp_client.is_message_sent_successfully(message_response):
                        user.last_prompt_sent = new_prompt_number
                        # Set last_question_asked to the random question's ID
                        user.last_question_asked = random_next_question.id
                        db.session.commit()
                        current_app.logger.info(f'Updated user last_prompt_sent to {new_prompt_number}')
                        return "OK"
                    else:
                        current_app.logger.error(f"Failed to send message to {parsed_message['from_field']}: {message_response.status_code} - {message_response.text}")
                        return "Failed to send question message", 500

                # The next question is not from a "group_type": "select" or "group_type": "random" group
                # This means it's from a "sequential" group or has no group
                else:
                    # Send next question via WhatsApp
                    question_data = {
                        "question_type": next_question.question_type,
                        "text": next_question.prompt,
                        "options": next_question.options or {}
                    }
                    current_app.logger.info(f'Sending question data: {question_data}')
                    whatsapp_client = WhatsAppClient()
                    message_response = whatsapp_client.send_question_message(parsed_message["from_field"], question_data)
                    # Update user's last_prompt_sent if message was sent successfully
                    if whatsapp_client.is_message_sent_successfully(message_response):
                        user.last_prompt_sent = new_prompt_number
                        # Set last_question_asked to the question's ID (for sequential groups or no group)
                        user.last_question_asked = next_question.id
                        db.session.commit()
                        current_app.logger.info(f'Updated user last_prompt_sent to {new_prompt_number}')
                        return "OK"
                    else:
                        current_app.logger.error(f"Failed to send message to {parsed_message['from_field']}: {message_response.status_code} - {message_response.text}")
                        return "Failed to send question message", 500
