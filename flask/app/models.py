"""
SQLAlchemy models
"""
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.database import db

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    #token = db.Column(db.String(255), unique=True, nullable=False)
    token = db.Column(db.String(255), nullable=True)
    username = db.Column(db.String(255), unique=True, nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    user_ids = db.Column(db.Text, nullable=True)
    survey_name = db.Column(db.String(50), nullable=True, default=None)
    last_prompt_sent = db.Column(db.Integer, nullable=True, default=None)
    last_question_asked = db.Column(db.Integer, nullable=True)

    emirati_citizenship = db.Column(db.Boolean, nullable=True, default=None)
    age_group = db.Column(db.Integer, nullable=True, default=None)
    gender = db.Column(db.Text, nullable=True)
    place_of_birth = db.Column(db.Text, nullable=True)
    current_residence = db.Column(db.Text, nullable=True)
    dialect_description = db.Column(db.Text, nullable=True)

    real_name_optional_input = db.Column(db.Text, nullable=True)
    phone_number_optional_input = db.Column(db.Text, nullable=True)

    consent_read_form = db.Column(db.Boolean, nullable=True, default=None)
    consent_required = db.Column(db.Boolean, nullable=True, default=None)
    consent_optional = db.Column(db.Boolean, nullable=True, default=None)
    consent_required_2 = db.Column(db.Boolean, nullable=True, default=None)
    consent_optional_alternative = db.Column(db.Boolean, nullable=True, default=None)
    demographics_and_consent_completed = db.Column(db.Boolean, nullable=False, default=False)

    phone_number = db.Column(db.String(50), unique=True, nullable=True)  
    
    delete_data_token = db.Column(db.String(6), unique=True, nullable=True)

    # Relationships
    responses = db.relationship('Response', backref='user', lazy=True, cascade='all, delete-orphan')
    surveys = db.relationship('Survey', secondary='survey_user_associations', backref='users', lazy='dynamic')
    progress_entries = db.relationship('Progress', back_populates='user', lazy=True, overlaps="surveys,users")

    def set_password(self, password):
        """Set password hash"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if provided password matches the hash"""
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'
    
class QuestionGroupType:
    SEQUENTIAL = 'sequential' # Questions are asked in order
    RANDOM = 'random'   # Questions are asked in random order
    SELECT = 'select'  # User selects which question to answer next

class QuestionGroup(db.Model):
    __tablename__ = 'question_groups'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    group_type = db.Column(db.String(50), nullable=False, default=QuestionGroupType.SEQUENTIAL)
    prompt_number = db.Column(db.Integer, nullable=True)  # Sequential prompt number (for WhatsApp surveys)
    survey_id = db.Column(db.Integer, db.ForeignKey('surveys.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    
    # Relationships
    questions = db.relationship('Question', backref='question_group', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<QuestionGroup {self.name}>'


class QuestionType:
    RADIO = 'radio'
    CHECKBOX = 'checkbox'
    TEXT = 'text'
    AUDIO = 'audio'
    INTERACTIVE = 'interactive'  

class Question(db.Model):
    __tablename__ = 'questions'
    
    id = db.Column(db.Integer, primary_key=True)
    prompt = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(50), nullable=False)
    response_type = db.Column(db.String(50), nullable=False)
    question_group_id = db.Column(db.Integer, db.ForeignKey('question_groups.id', ondelete='SET NULL'), nullable=True)
    survey_id = db.Column(db.Integer, db.ForeignKey('surveys.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    question_metadata = db.Column('metadata', db.JSON, nullable=True)  

    survey_name = db.Column(db.String(50), nullable=True, default=None)

    prompt_number = db.Column(db.Integer, nullable=True)  # Sequential prompt number (for WhatsApp surveys)
    options = db.Column(db.JSON, nullable=True)  # For radio/checkbox questions, store options as JSON
    required = db.Column(db.Boolean, nullable=False, default=True)  # Whether the question is required
    
    # Relationships
    # Note: 'question_group' relationship is provided via backref from QuestionGroup.questions
    responses = db.relationship('Response', backref='question', lazy=True, cascade='all, delete-orphan')
    progress_entries = db.relationship('Progress', backref='current_question', lazy=True)
    
    def __repr__(self):
        return f'<Question {self.id}: {self.prompt[:50]}...>'

class Response(db.Model):
    __tablename__ = 'responses'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    question_id = db.Column(db.Integer, db.ForeignKey('questions.id', ondelete='CASCADE'), nullable=False)
    response_type = db.Column(db.String(50), nullable=False)
    response_value = db.Column(db.Text, nullable=True)
    file_path = db.Column(db.String(500), nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    # WhatsApp-specific metadata (for media files, interactive responses)
    # 'metadata' is a reserved attribute name on declarative models, so map
    # the DB column named 'metadata' to the attribute `response_metadata`.
    response_metadata = db.Column('metadata', db.JSON, nullable=True)
    
    def __repr__(self):
        return f'<Response {self.id}: {self.response_type}>'


class Survey(db.Model):
    """
    Stores survey definitions
    """
    __tablename__ = 'surveys'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    consent_form = db.Column(db.JSON, nullable=True)  # Store consent form details as JSON
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    questions = db.relationship('Question', backref='survey', lazy=True, cascade='all, delete-orphan')
    question_groups = db.relationship('QuestionGroup', backref='survey', lazy=True, cascade='all, delete-orphan')
    progress_entries = db.relationship('Progress', back_populates='survey', lazy=True, overlaps="users")
    
    def __repr__(self):
        return f'<Survey {self.name}>'

class SurveyLogic(db.Model):
    """
    Stores conditional logic for survey navigation
    Maps button/list responses to the next question to show
    """
    __tablename__ = 'survey_logic'
    
    id = db.Column(db.Integer, primary_key=True)
    survey_id = db.Column('survey', db.Integer, db.ForeignKey('surveys.id', ondelete='CASCADE'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('questions.id', ondelete='CASCADE'), nullable=True)
    question_group_id = db.Column(db.Integer, db.ForeignKey('question_groups.id', ondelete='CASCADE'), nullable=True)
    response_option_id = db.Column(db.String(100), nullable=False)  # The button/list item ID
    next_question_id = db.Column(db.Integer, db.ForeignKey('questions.id', ondelete='CASCADE'), nullable=True)
    
    # Relationships
    survey = db.relationship('Survey', backref='logic_rules', lazy=True)
    question = db.relationship('Question', foreign_keys=[question_id], backref='logic_rules')
    question_group = db.relationship('QuestionGroup', backref='logic_rules', lazy=True)
    next_question = db.relationship('Question', foreign_keys=[next_question_id], backref='next_logic_rules')
    
    def __repr__(self):
        survey_name = self.survey.name if self.survey else f"Survey {self.survey_id}"
        return f'<SurveyLogic {survey_name}: {self.response_option_id} -> Q{self.next_question_id}>'


class Progress(db.Model):
    """
    Association table for many-to-many relationship between Users and Surveys
    Tracks user progress through surveys
    """
    __tablename__ = 'survey_user_associations'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    survey_id = db.Column(db.Integer, db.ForeignKey('surveys.id', ondelete='CASCADE'), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    current_question_id = db.Column(db.Integer, db.ForeignKey('questions.id', ondelete='SET NULL'), nullable=True)

    # Relationships
    user = db.relationship('User', back_populates='progress_entries', lazy=True)
    survey = db.relationship('Survey', back_populates='progress_entries', lazy=True)
    # current_question relationship is provided via backref from Question.progress_entries

    def __repr__(self):
        return f'<Progress User {self.user_id} Survey {self.survey_id} Q{self.current_question_id}>'
