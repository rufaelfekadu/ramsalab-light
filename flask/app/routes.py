import os
import random
import time
import shutil

from flask import (
    Blueprint, current_app, render_template, request, redirect,
    url_for, jsonify, flash, session, send_from_directory, send_file
)
from flask_login import login_user, logout_user, login_required, current_user
import tempfile
from datetime import datetime

from app import csrf
from app.utils import allowed_file, save_audio_file
from app.database import db
from app.models import User, Question, Response, Survey
from app.route_helpers import (
    get_user_from_request,
    get_or_create_anonymous_user,
    create_new_user_token,
    generate_unique_deletion_token
)
from app.export_utils import generate_csv, collect_audio_files, create_export_zip
from app.audino_client import AudinoClient

bp = Blueprint("routes", __name__)


# ============================================================================
# Authentication Routes
# ============================================================================

@bp.route("/login", methods=["GET", "POST"])
def login():
    """Login page and handler"""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        remember = request.form.get("remember") == "true"
        
        if not username or not password:
            flash("Please provide both username and password.", "error")
            return render_template("login.html")
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get("next")
            if next_page:
                return redirect(next_page)
            flash("Login successful!", "success")
            return redirect(url_for("routes.dashboard"))
        else:
            flash("Invalid username or password.", "error")
    
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    """Logout handler"""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("routes.index"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Registration is disabled - users must be manually added by administrators"""
    flash("Registration is not available. Please contact an administrator for access.", "error")
    return redirect(url_for("routes.login"))


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
    return render_template("index.html", current_user=None)

@bp.route("/consent-form")
def consent_form():
    """Consent form page"""
    # Get or create user when they visit the consent form
    user = get_or_create_anonymous_user()
    
    return render_template("consent-form.html", current_user=None)


@bp.route("/demography")
def demography():
    """Demographic information collection page"""
    # Verify user exists in session
    user = get_user_from_request()
    
    if not user:
        flash("يرجى البدء من الصفحة الرئيسية.", "error")
        return redirect(url_for('routes.index'))
    
    return render_template("demography.html", current_user=None)


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
                         current_user=None)


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
                         current_user=None)


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
                         current_user=None,
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
# File Serving Routes
# ============================================================================

@bp.route("/uploads/<path:question_id>/<path:filename>")
def serve_upload(question_id, filename):
    """
    Serve uploaded files from S3 or local storage.
    If file_path in database is an S3 URL, generate presigned URL.
    Otherwise, serve from local storage.
    """
    # Try to find the response by question_id and filename
    # First, try to find by matching the file_path
    response = Response.query.filter_by(question_id=question_id).first()
    
    if response and response.file_path:
        # Check if it's an S3 URL
        if response.file_path.startswith('http://') or response.file_path.startswith('https://'):
            # Generate presigned URL for S3
            try:
                import boto3
                from botocore.exceptions import ClientError
                
                # Extract S3 key from URL
                # URL format: https://bucket.s3.region.amazonaws.com/question_id/filename
                s3_url_parts = response.file_path.replace('https://', '').replace('http://', '').split('/', 1)
                if len(s3_url_parts) == 2:
                    s3_key = s3_url_parts[1]
                    
                    s3_client = boto3.client(
                        's3',
                        aws_access_key_id=current_app.config.get("AWS_ACCESS_KEY_ID"),
                        aws_secret_access_key=current_app.config.get("AWS_SECRET_ACCESS_KEY"),
                        region_name=current_app.config.get("AWS_REGION")
                    )
                    
                    # Generate presigned URL (valid for 1 hour)
                    presigned_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={
                            'Bucket': current_app.config.get("AWS_S3_BUCKET"),
                            'Key': s3_key
                        },
                        ExpiresIn=3600
                    )
                    return redirect(presigned_url)
            except (ClientError, Exception) as e:
                current_app.logger.error(f"Error generating S3 presigned URL: {e}")
                return "File not found", 404
    
    # Fall back to local file serving
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    question_folder = os.path.join(upload_folder, question_id)
    return send_from_directory(question_folder, filename)




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
        
        # save_audio_file returns either S3 URL or relative path (question_id/filename)
        file_path = save_audio_file(audio, str(user.id), str(question_id))
        
        # Create response
        response = Response(
            user_id=user.id,
            question_id=question_id,
            response_type="audio",
            file_path=file_path  # Store S3 URL or relative path directly
        )
        db.session.add(response)
        db.session.commit()
        
        current_app.logger.info(f'Audio submitted by user {user.id} for question {question_id}, stored at {file_path}')

        # Create task for Audino integration (if enabled)
        if current_app.config.get("ANNOTATION_TASK_CREATION_ENABLED"):
            try:
                # Get the question object for metadata
                question = Question.query.get(question_id)
                if not question:
                    current_app.logger.warning(f'Question {question_id} not found for Audino task creation')
                else:
                    # Initialize Audino client
                    audino_api_url = current_app.config.get("AUDINO_API_URL", "").rstrip('/')
                    audino_api_key = current_app.config.get("ANNOTATION_API_KEY", "")
                    
                    if not audino_api_url or not audino_api_key:
                        current_app.logger.warning("Audino API URL or API key not configured")
                    else:
                        audino_client = AudinoClient(audino_api_url, audino_api_key)
                        
                        # Check if API is available
                        if not audino_client.is_available():
                            current_app.logger.warning("Audino API is not available")
                        else:
                            # Prepare task data
                            task_name = f"Response_{response.id}"
                            task_data = {
                                "name": task_name,
                                "subset": "train",
                                "response_id": response.id,  # Link back to ramsalab response
                                "response_demographics": {
                                    "user_id": user.id,
                                    "question_id": question_id,
                                    "question_prompt": question.prompt,
                                    "survey_id": int(survey_id) if survey_id else None,
                                }
                            }
                            
                            # Add optional fields if configured
                            if current_app.config.get("AUDINO_PROJECT_ID"):
                                task_data["project_id"] = int(current_app.config.get("AUDINO_PROJECT_ID"))
                            
                            if current_app.config.get("AUDINO_ASSIGNEE_ID"):
                                task_data["assignee_id"] = int(current_app.config.get("AUDINO_ASSIGNEE_ID"))
                            
                            # Create task in Audino
                            task_id = audino_client.create_task(task_data)
                            
                            if task_id:
                                # Store the task ID in the response
                                response.audino_task_id = task_id
                                db.session.commit()
                                current_app.logger.info(f'Successfully created Audino task {task_id} for response {response.id}')
                                
                                # Try to upload the audio file if it's a local path
                                if not file_path.startswith('http'):
                                    import os
                                    if os.path.exists(file_path):
                                        audino_client.upload_file(task_id, file_path, os.path.basename(file_path))
                                    else:
                                        current_app.logger.warning(f'Local file not found for upload: {file_path}')
                            else:
                                current_app.logger.error(f'Failed to create Audino task for response {response.id}')
                                # Don't fail the submission, just log the error
                                
            except Exception as e:
                current_app.logger.error(f'Error creating Audino task for response {response.id}: {e}', exc_info=True)
                # Don't fail the submission if task creation fails
        
        # Extract filename for response (basename of path or URL)
        filename = os.path.basename(file_path) if not file_path.startswith('http') else os.path.basename(file_path.split('?')[0])
        
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
# Dashboard Routes
# ============================================================================

@bp.route("/dashboard")
@login_required
def dashboard():
    """Dashboard page for data export with filters"""
    try:
        # Get all surveys and questions for filter options
        surveys = Survey.query.order_by(Survey.id).all()
        questions = Question.query.filter_by(active=True).order_by(Question.id).all()
        
        # Group questions by survey for better UI
        questions_by_survey = {}
        for question in questions:
            survey_id = question.survey_id
            if survey_id not in questions_by_survey:
                questions_by_survey[survey_id] = []
            questions_by_survey[survey_id].append(question)
        
        return render_template(
            "dashboard.html",
            surveys=surveys,
            questions=questions,
            questions_by_survey=questions_by_survey,
            current_user=current_user
        )
    except Exception as e:
        current_app.logger.error(f"Error loading dashboard: {e}", exc_info=True)
        flash("An error occurred while loading the dashboard.", "error")
        return redirect(url_for("routes.index"))


@bp.route("/dashboard/preview", methods=["POST"])
@login_required
@csrf.exempt  # Exempt from CSRF since it's already protected by @login_required
def dashboard_preview():
    """Get preview of filtered responses (limited to 20 rows)"""
    try:
        # Get filter parameters from JSON
        data = request.get_json() or {}
        survey_ids = data.get('survey_ids', [])
        question_ids = data.get('question_ids', [])
        date_from = data.get('date_from')
        date_to = data.get('date_to')
        
        # Convert to integers
        try:
            survey_ids = [int(sid) for sid in survey_ids if sid]
            question_ids = [int(qid) for qid in question_ids if qid]
        except (ValueError, TypeError) as e:
            current_app.logger.error(f"Invalid filter parameters: {e}")
            return jsonify({"error": "Invalid filter parameters"}), 400
        
        # Build query with proper joins
        query = db.session.query(Response).join(Question).join(Survey)
        
        # Apply filters
        if survey_ids:
            query = query.filter(Survey.id.in_(survey_ids))
        
        if question_ids:
            query = query.filter(Question.id.in_(question_ids))
        
        # Apply date filters
        if date_from:
            try:
                from datetime import datetime as dt
                date_from_obj = dt.strptime(date_from, '%Y-%m-%d')
                query = query.filter(Response.timestamp >= date_from_obj)
            except ValueError as e:
                current_app.logger.error(f"Invalid date_from format: {e}")
        
        if date_to:
            try:
                from datetime import datetime as dt
                # Add one day to include the entire end date
                date_to_obj = dt.strptime(date_to, '%Y-%m-%d')
                from datetime import timedelta
                date_to_obj = date_to_obj + timedelta(days=1)
                query = query.filter(Response.timestamp < date_to_obj)
            except ValueError as e:
                current_app.logger.error(f"Invalid date_to format: {e}")
        
        # Get total count
        total_count = query.count()
        
        # Get limited preview (max 20 rows)
        preview_responses = query.limit(20).all()
        
        # Format preview data
        preview_data = []
        for response in preview_responses:
            user = response.user if hasattr(response, 'user') else None
            question = response.question if hasattr(response, 'question') else None
            survey = question.survey if question and hasattr(question, 'survey') else None
            
            preview_data.append({
                'response_id': response.id,
                'user_id': response.user_id,
                'question_prompt': question.prompt[:100] + '...' if question and len(question.prompt) > 100 else (question.prompt if question else ''),
                'survey_name': survey.name if survey else '',
                'response_type': response.response_type,
                'response_value': (response.response_value[:50] + '...' if response.response_value and len(response.response_value) > 50 else response.response_value) or '',
                'timestamp': response.timestamp.isoformat() if response.timestamp else '',
                'has_audio': bool(response.file_path and response.response_type == 'audio')
            })
        
        return jsonify({
            'total_count': total_count,
            'preview_count': len(preview_data),
            'preview': preview_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Error getting preview: {e}", exc_info=True)
        return jsonify({"error": "An error occurred while getting preview"}), 500


@bp.route("/dashboard/export", methods=["POST"])
@login_required
def dashboard_export():
    """Export filtered survey data as CSV and zip with audio files"""
    try:
        # Get filter parameters
        survey_ids = request.form.getlist('survey_ids[]')
        question_ids = request.form.getlist('question_ids[]')
        date_from = request.form.get('date_from')
        date_to = request.form.get('date_to')
        
        # Convert to integers
        try:
            survey_ids = [int(sid) for sid in survey_ids if sid]
            question_ids = [int(qid) for qid in question_ids if qid]
        except (ValueError, TypeError) as e:
            current_app.logger.error(f"Invalid filter parameters: {e}")
            flash("Invalid filter parameters.", "error")
            return redirect(url_for("routes.dashboard"))
        
        # Build query with proper joins
        query = db.session.query(Response).join(Question).join(Survey)
        
        # Apply filters
        if survey_ids:
            query = query.filter(Survey.id.in_(survey_ids))
        
        if question_ids:
            query = query.filter(Question.id.in_(question_ids))
        
        # Apply date filters
        if date_from:
            try:
                from datetime import datetime as dt
                date_from_obj = dt.strptime(date_from, '%Y-%m-%d')
                query = query.filter(Response.timestamp >= date_from_obj)
            except ValueError as e:
                current_app.logger.error(f"Invalid date_from format: {e}")
        
        if date_to:
            try:
                from datetime import datetime as dt
                from datetime import timedelta
                date_to_obj = dt.strptime(date_to, '%Y-%m-%d')
                # Add one day to include the entire end date
                date_to_obj = date_to_obj + timedelta(days=1)
                query = query.filter(Response.timestamp < date_to_obj)
            except ValueError as e:
                current_app.logger.error(f"Invalid date_to format: {e}")
        
        # Get all responses
        # SQLAlchemy relationships will automatically load User, Question, Survey
        responses = query.all()
        
        if not responses:
            flash("No responses found matching the selected filters.", "info")
            return redirect(url_for("routes.dashboard"))
        
        current_app.logger.info(f"Exporting {len(responses)} responses for user {current_user.id}")
        
        # Create temporary directory for files
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Generate CSV
            csv_path = os.path.join(temp_dir, "survey_responses.csv")
            generate_csv(responses, csv_path)
            
            # Collect audio files
            audio_files = collect_audio_files(responses, temp_dir)
            
            # Create zip file
            zip_filename = f"survey_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            zip_path = os.path.join(temp_dir, zip_filename)
            create_export_zip(responses, csv_path, audio_files, zip_path, temp_dir)
            
            # Send file
            return send_file(
                zip_path,
                mimetype='application/zip',
                as_attachment=True,
                download_name=zip_filename
            )
            
        finally:
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                current_app.logger.warning(f"Error cleaning up temp directory: {e}")
        
    except Exception as e:
        current_app.logger.error(f"Error exporting data: {e}", exc_info=True)
        flash("An error occurred while exporting data.", "error")
        return redirect(url_for("routes.dashboard"))


# ============================================================================
# API Routes for Audino Integration
# ============================================================================

# @bp.route("/api/responses/for-task-creation", methods=["POST"])
# @csrf.exempt
# def get_responses_for_task_creation():
#     """API endpoint to fetch audio responses for task creation in Audino"""
#     try:
#         # Validate API key - support both X-API-Key header and Authorization header with Token format
#         api_key = request.headers.get("X-API-Key")
        
#         # If X-API-Key is not present, try Authorization header with Token format
#         if not api_key:
#             auth_header = request.headers.get("Authorization", "")
#             if auth_header.startswith("Token "):
#                 api_key = auth_header.replace("Token ", "", 1).strip()
        
#         expected_api_key = current_app.config.get("RAMSALAB_API_KEY")
        
#         if not expected_api_key:
#             current_app.logger.error("RAMSALAB_API_KEY not configured")
#             return jsonify({"error": "API key not configured on server"}), 500
        
#         if not api_key or api_key != expected_api_key:
#             current_app.logger.warning(f"Invalid API key attempt: {api_key[:10] if api_key else 'None'}...")
#             return jsonify({"error": "Invalid API key"}), 401
        
#         # Get filter parameters from JSON body
#         data = request.get_json() or {}
#         question_ids = data.get("question_ids", [])
#         survey_ids = data.get("survey_ids", [])
#         user_ids = data.get("user_ids", [])
#         date_from = data.get("date_from")
#         date_to = data.get("date_to")
        
#         # Convert to integers and validate
#         try:
#             if question_ids:
#                 question_ids = [int(qid) for qid in question_ids if qid]
#             if survey_ids:
#                 survey_ids = [int(sid) for sid in survey_ids if sid]
#             if user_ids:
#                 user_ids = [int(uid) for uid in user_ids if uid]
#         except (ValueError, TypeError) as e:
#             current_app.logger.error(f"Invalid filter parameters: {e}")
#             return jsonify({"error": "Invalid filter parameters. IDs must be integers."}), 400
        
#         # Build query - only get audio responses
#         query = db.session.query(Response).join(Question).join(Survey).filter(
#             Response.response_type == "audio",
#             Response.file_path.isnot(None),
#             Response.file_path != ""
#         )
        
#         # Apply filters
#         if question_ids:
#             query = query.filter(Question.id.in_(question_ids))
        
#         if survey_ids:
#             query = query.filter(Survey.id.in_(survey_ids))
        
#         if user_ids:
#             query = query.filter(Response.user_id.in_(user_ids))
        
#         # Apply date filters
#         if date_from:
#             try:
#                 from datetime import datetime as dt
#                 date_from_obj = dt.strptime(date_from, "%Y-%m-%d")
#                 query = query.filter(Response.timestamp >= date_from_obj)
#             except ValueError as e:
#                 current_app.logger.error(f"Invalid date_from format: {e}")
#                 return jsonify({"error": f"Invalid date_from format. Use YYYY-MM-DD"}), 400
        
#         if date_to:
#             try:
#                 from datetime import datetime as dt, timedelta
#                 date_to_obj = dt.strptime(date_to, "%Y-%m-%d")
#                 # Add one day to include the entire end date
#                 date_to_obj = date_to_obj + timedelta(days=1)
#                 query = query.filter(Response.timestamp < date_to_obj)
#             except ValueError as e:
#                 current_app.logger.error(f"Invalid date_to format: {e}")
#                 return jsonify({"error": f"Invalid date_to format. Use YYYY-MM-DD"}), 400
        
#         # Get total count
#         total_count = query.count()
        
#         # Get all matching responses
#         responses = query.order_by(Response.timestamp.desc()).all()
        
#         # Format response data
#         response_data = []
#         for response in responses:
#             question = response.question if hasattr(response, "question") else None
#             survey = question.survey if question and hasattr(question, "survey") else None
            
#             # Only include responses with valid file paths (S3 URLs or local paths)
#             file_path = response.file_path
#             if not file_path:
#                 continue
            
#             response_data.append({
#                 "id": response.id,
#                 "question_id": response.question_id,
#                 "question_prompt": question.prompt if question else "",
#                 "survey_id": survey.id if survey else None,
#                 "survey_name": survey.name if survey else "",
#                 "user_id": response.user_id,
#                 "file_path": file_path,  # S3 URL or local path
#                 "timestamp": response.timestamp.isoformat() if response.timestamp else None,
#                 "response_metadata": response.response_metadata or {},
#             })
        
#         current_app.logger.info(f"API: Fetched {len(response_data)} responses for task creation (total matching: {total_count})")
        
#         return jsonify({
#             "responses": response_data,
#             "total": len(response_data),
#         })
        
#     except Exception as e:
#         current_app.logger.error(f"Error in get_responses_for_task_creation: {e}", exc_info=True)
#         return jsonify({"error": "An error occurred while fetching responses"}), 500


