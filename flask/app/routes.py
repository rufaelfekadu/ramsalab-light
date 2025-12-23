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
from app.whatsapp_utils import WhatsAppClient
from app.database import db
from app.models import QuestionGroup, User, Question, Response, Survey, Progress
from app.route_helpers import (
    get_user_from_request,
    get_or_create_anonymous_user,
    validate_and_get_user_id,
    delete_response_files,
    send_whatsapp_deletion_notification,
    create_new_user_token,
    generate_unique_deletion_token
)
from app.whatsapp_handlers import handle_whatsapp_webhook


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


# ============================================================================
# Helper Functions
# ============================================================================

def token_to_6_digits(token, secret_key=None):
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


# ============================================================================
# Authentication Routes
# ============================================================================

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

@bp.route("/update_all_consent_data", methods=["POST"])
def update_all_consent_data():
    """Update all consent and demographic data in database at once"""
    try:
        # Get user - either from current_user if logged in, or from user_id parameter
        if current_user.is_authenticated:
            user = current_user
        else:
            user_id, error_response = validate_and_get_user_id()
            if error_response:
                return error_response
            user = User.query.filter_by(id=user_id).first()
            if not user:
                return jsonify({"status": "error", "message": "User not found"}), 404
        
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


# ============================================================================
# Survey Flow Routes
# ============================================================================

# @bp.route("/participant-information")
# def participant_information():
#     """Display the Participant Information Sheet"""
#     return render_template("participant_information.html")

@bp.route("/")
def index():
    """Landing page"""
    # delete the session and start a new one
    session.clear()
    return render_template("index.html", current_user=current_user)

@bp.route("/consent-form")
def consent_form():
    """Consent form page"""
    # Get or create user when they visit the consent form
    if current_user.is_authenticated:
        user = current_user
    else:
        user = get_or_create_anonymous_user()
    
    return render_template("consent-form.html", current_user=current_user)


@bp.route("/demography")
def demography():
    """Demographic information collection page"""
    # Verify user exists in session
    user = get_user_from_request()
    
    if not user:
        flash("يرجى البدء من الصفحة الرئيسية.", "error")
        return redirect(url_for('routes.index'))
    
    return render_template("demography.html", current_user=current_user)


@bp.route("/select-theme", methods=["GET", "POST"])
def select_theme():
    """Theme/category selection page - displays surveys from database"""
    
    # Handle POST requests: validate user and survey, select random question
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
                user = get_user_from_request()

            
            # Validate user exists
            if not user:
                flash("حدث خطأ في التحقق من المستخدم.", "error")
                return redirect(url_for('routes.index'))
            
            # Get all questions for the survey (only active ones)
            all_questions = Question.query.filter_by(survey_id=survey.id, active=True).all()
            
            if not all_questions:
                flash("لا توجد أسئلة في هذا الاستطلاع.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Get seen question IDs from session
            seen_key = f'seen_questions_{survey_id}'
            seen_ids = session.get(seen_key, [])
            
            # Get all question IDs
            all_question_ids = [q.id for q in all_questions]
            
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
            current_question = Question.query.filter_by(id=selected_question_id, active=True).first()
            
            if not current_question:
                flash("لا توجد أسئلة في هذا الاستطلاع.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Mark selected question as seen in session
            seen_ids.append(selected_question_id)
            session[seen_key] = seen_ids
            
            current_app.logger.info(f'Selected random question {current_question.id} from survey {survey.id} for user {user.id}')
            
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
            current_app.logger.error(f"Error selecting question: {e}", exc_info=True)
            flash("حدث خطأ أثناء اختيار السؤال. يرجى المحاولة مرة أخرى.", "error")
            return redirect(url_for('routes.select_theme'))
    
    # Handle GET requests: fetch and display all surveys
    # Verify user exists in session
    user = get_user_from_request()
    
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
    user = get_user_from_request()
    
    if not user:
        flash("يرجى البدء من الصفحة الرئيسية.", "error")
        return redirect(url_for('routes.index'))
    
    # If question_id is provided, fetch the question from database
    if question_id:
        try:
            question_id = int(question_id)
            question = Question.query.filter_by(id=question_id, active=True).first()
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
        user = get_user_from_request()
        
        if not user:
            flash("يرجى البدء من الصفحة الرئيسية.", "error")
            return redirect(url_for('routes.index'))
        
        try:
            survey_id = int(survey_id)
            survey = Survey.query.filter_by(id=survey_id).first()
            
            if not survey:
                flash("الاستطلاع المحدد غير موجود.", "error")
                return redirect(url_for('routes.select_theme'))
            
            # Get current question for exclusion
            current_question = None
            current_question_id_int = None
            if current_question_id:
                try:
                    current_question_id_int = int(current_question_id)
                    current_question = Question.query.filter_by(
                        id=current_question_id_int,
                        survey_id=survey.id,
                        active=True
                    ).first()
                except (ValueError, TypeError):
                    pass
            
            # Get all questions for the survey (only active ones)
            all_questions = Question.query.filter_by(survey_id=survey.id, active=True).all()
            
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
            next_question = Question.query.filter_by(id=next_question_id, active=True).first()
            
            if not next_question:
                flash("لا توجد أسئلة متاحة في هذا الاستطلاع.", "error")
                return redirect(url_for('routes.select_theme'))
            
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
    user = get_user_from_request()
    
    token = user.token if user else None
    token_6_digits = None

    # Generate and store unique deletion token if user exists and doesn't have one
    if user:
        if not user.delete_data_token:
            try:
                user.delete_data_token = generate_unique_deletion_token()
                db.session.commit()
                current_app.logger.info(f'Generated deletion token for user {user.id}: {user.delete_data_token}')
            except Exception as e:
                current_app.logger.error(f'Error generating deletion token for user {user.id}: {e}', exc_info=True)
                db.session.rollback()
        
        token_6_digits = user.delete_data_token
    
    return render_template("thanks.html", 
                         current_user=current_user,
                         user_token=token,
                         token_6_digits=token_6_digits)


# ============================================================================
# Data Update Routes
# ============================================================================

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
        if current_user.is_authenticated:
            user = current_user
        else:
            user = get_or_create_anonymous_user()
        
        # Update demographic fields
        if emirati_citizenship:
            user.emirati_citizenship = emirati_citizenship.lower() == 'yes'
        if age_group:
            # Store age_group as string for now, or convert to integer if needed
            # The model expects Integer, but we're getting string values like "18-25"
            # For now, we'll store it as a string in a text field or handle conversion
            # Since age_group is Integer in model, we'll need to map string to int
            age_mapping = {
                "18-20": 1,
                "21-25": 2,
                "26-35": 3,
                "36-45": 4,
                "46-55": 5,
                "56-65": 6,
                "65+": 7
            }
            user.age_group = age_mapping.get(age_group, None)

        # Note: gender field doesn't exist in User model, skipping for now
        if gender:
            user.gender = gender
        if place_of_birth:
            user.place_of_birth = place_of_birth
        if current_residence:
            user.current_residence = current_residence

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

@bp.route("/update_consent", methods=["POST"])
def update_consent():
    """Update user's consent field in database"""
    try:
        # Get consent values from form - support both old and new field names for backward compatibility
        consent_read_form = request.form.get("consent_read_form") == "on" 
        consent_required = request.form.get("consent_required") == "on"
        consent_required_2 = request.form.get("consent_required_2") == "on"
        consent_optional = request.form.get("consent_optional") == "on" or request.form.get("consent_data") == "on"
        
        # Validate required consents (consent_read_form, consent_required, and consent_required_2 are required)
        if not (consent_read_form and consent_required and consent_required_2):
            flash("يرجى الموافقة على جميع الإقرارات المطلوبة (المميزة بـ *)", "error")
            return redirect(url_for('routes.consent_form'))
        
        # Get or create user
        if current_user.is_authenticated:
            user = current_user
        else:
            user = get_or_create_anonymous_user()
        
        # Update consent fields
        user.consent_read_form = consent_read_form
        user.consent_required = consent_required
        user.consent_optional = consent_optional
        user.consent_required_2 = consent_required_2
        
        db.session.commit()
        current_app.logger.info(f'Updated consent for user {user.id}')
        
        # Redirect to demography page
        return redirect(url_for('routes.demography'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating consent: {e}", exc_info=True)
        flash("حدث خطأ أثناء حفظ البيانات. يرجى المحاولة مرة أخرى.", "error")
        return redirect(url_for('routes.consent_form'))


# ============================================================================
# User Management Routes
# ============================================================================

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


# ============================================================================
# File Serving Routes
# ============================================================================

@bp.route("/uploads/<path:question_id>/<path:filename>")
def serve_upload(question_id, filename):
    """Serve uploaded files"""
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    question_folder = os.path.join(upload_folder, question_id)
    return send_from_directory(question_folder, filename)


# ============================================================================
# Data Management Routes
# ============================================================================

@bp.route("/delete_survey_data", methods=["POST"])
def delete_survey_data():
    """Delete all responses for a specific survey session (user_id)"""
    user_id, error_response = validate_and_get_user_id()
    if error_response:
        return error_response
    
    try:
        # Get the user to check for phone_number
        user = User.query.filter_by(id=user_id).first()
        
        # Get all responses for this user_id
        responses = Response.query.filter_by(user_id=user_id).all()
        
        # Delete associated files using helper
        upload_folder = current_app.config.get("UPLOAD_FOLDER", "_uploads")
        delete_response_files(responses, upload_folder)
        
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
        if user:
            send_whatsapp_deletion_notification(user.phone_number, user_id)
        
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
            
            # Delete associated files using helper
            upload_folder = current_app.config.get("UPLOAD_FOLDER", "_uploads")
            delete_response_files(responses, upload_folder)
            
            # Delete progress entries
            progress_entries = Progress.query.filter_by(user_id=user_id).all()
            for progress in progress_entries:
                db.session.delete(progress)
            
            # Send WhatsApp notification if user has a phone number
            send_whatsapp_deletion_notification(user.phone_number, user_id)
            
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
        
        # Delete associated files using helper
        upload_folder = current_app.config.get("UPLOAD_FOLDER", "_uploads")
        delete_response_files(responses, upload_folder)
        
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
        send_whatsapp_deletion_notification(current_user.phone_number, current_user.id)
        
        current_app.logger.info(f'Deleted all data for user {current_user.id}: {response_count} responses')
        return jsonify({"status": "success", "message": f"Deleted {response_count} responses"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting all data: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to delete data"}), 500


# ============================================================================
# Survey Routes
# ============================================================================

@bp.route("/survey", methods=["GET", "POST"])
def survey():
    user_id = request.args.get("user_id")
    survey_id = request.args.get("survey_id")
    
    if not user_id:
        # Create new anonymous user
        user = get_or_create_anonymous_user()
        
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
            survey_id=survey.id,
            active=True
        ).first() if progress.current_question_id else None
        current_question_group = current_question.question_group if current_question else None
        last_prompt_sent = current_question.prompt_number if current_question else None
    else:
        # create new progress entry - start with prompt_number 0
        current_question = Question.query.filter_by(
            survey_id=survey.id,
            prompt_number=0,
            active=True
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
                        question_group_id=current_question_group.id,
                        active=True
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
                Question.survey_id == survey.id,
                Question.active == True
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
                    prompt_number=next_prompt_number,
                    active=True
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
        user = get_user_from_request()
        
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


# ============================================================================
# WhatsApp Integration Routes
# ============================================================================

@bp.route("/whatsapp-webhook-endpoint", methods=["GET", "POST"])
@csrf.exempt
def whatsapp_webhook_endpoint():
    """
    WhatsApp webhook endpoint - thin wrapper that delegates to whatsapp_handlers module.
    """
    response, status_code = handle_whatsapp_webhook(
        request.json if request.is_json else {},
        request.method,
        request.args
    )
    return response, status_code
