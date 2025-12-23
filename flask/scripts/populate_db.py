#!/usr/bin/env python3
"""
Script to populate the database with questions from JSON file
"""
import os
import sys
import json

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.database import db
from app.models import Survey, Question, QuestionGroup, SurveyLogic
from dotenv import load_dotenv
load_dotenv()

def load_json(path):
    """Load JSON from file and return parsed object."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"Loaded JSON from {path}")
        return data
    except FileNotFoundError:
        print(f"Error: {path} not found!")
        return []
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON file: {e}")
        return []

def populate_questions(json_file="assets/surveys/english.json", append=False):
    """Populate the database with surveys/questions/logic from a survey JSON file.

    The JSON is expected to be a list of survey objects. For each survey:
    - If survey exists, keep it and update it (don't delete)
    - Questions are identified by prompt text within the survey
    - Questions in JSON are marked as active=True
    - Questions not in JSON are marked as active=False (disabled)
    - New questions from JSON are created
    - Existing questions from JSON are updated
    """
    app = create_app()

    with app.app_context():
        surveys_data = load_json(json_file)

        if not surveys_data:
            print("No surveys to add. Exiting.")
            return

        # Statistics tracking
        stats = {
            'surveys_created': 0,
            'surveys_updated': 0,
            'question_groups_created': 0,
            'question_groups_updated': 0,
            'questions_created': 0,
            'questions_updated': 0,
            'questions_disabled': 0,
            'logic_rules_created': 0
        }

        for survey_obj in surveys_data:
            survey_name = survey_obj.get("survey_name")
            if not survey_name:
                print("Skipping survey with no `survey_name`")
                continue

            # Find or create survey (keep existing, don't delete)
            survey = Survey.query.filter_by(name=survey_name).first()
            if survey:
                print(f"Updating existing survey '{survey_name}'")
                # Update survey fields
                survey.description = survey_obj.get("description")
                survey.consent_form = survey_obj.get("consent_form")
                stats['surveys_updated'] += 1
            else:
                print(f"Creating new survey '{survey_name}'")
                survey = Survey(
                    name=survey_name,
                    description=survey_obj.get("description"),
                    consent_form=survey_obj.get("consent_form")
                )
                db.session.add(survey)
                stats['surveys_created'] += 1
            
            db.session.flush()  # get survey.id

            # Mark all existing questions for this survey as inactive
            # They will be reactivated if found in JSON
            existing_questions = Question.query.filter_by(survey_id=survey.id).all()
            initial_question_count = len(existing_questions)
            for eq in existing_questions:
                eq.active = False
            print(f"Marked {initial_question_count} existing questions as inactive")

            # We'll map prompt_number -> question.id for linking logic
            prompt_to_qid = {}
            # Map question group name -> id for linking group-level logic
            group_name_to_id = {}

            # NOTE: This survey format places all questions inside `question_groups`.
            # Support for top-level `questions` is intentionally omitted because the
            # provided JSON (assets/surveys/english.json) does not use it. If a survey
            # does include top-level questions in future, they will be ignored by this
            # script unless this section is re-added.

            # Process question groups and their questions
            for g in survey_obj.get("question_groups", []):
                group_name = g.get("name")
                group_full_name = f'group_{survey.id}_{group_name}' if group_name else f"group_{survey.id}_{len(g.get('questions',[]))}"
                
                # Find or create question group
                group = QuestionGroup.query.filter_by(name=group_full_name, survey_id=survey.id).first()
                if group:
                    # Update existing group
                    group.description = g.get("description")
                    group.group_type = g.get("group_type", "sequential")
                    group.prompt_number = g.get("prompt_number")
                    stats['question_groups_updated'] += 1
                else:
                    # Create new group
                    group = QuestionGroup(
                        name=group_full_name,
                        description=g.get("description"),
                        group_type=g.get("group_type", "sequential"),
                        prompt_number=g.get("prompt_number"),
                        survey_id=survey.id
                    )
                    db.session.add(group)
                    stats['question_groups_created'] += 1
                
                db.session.flush()
                # record group id for later linking when creating SurveyLogic
                group_name_to_id[group.name] = group.id

                # Process questions in this group
                for q in g.get("questions", []):
                    prompt_text = q.get("prompt", "")
                    if not prompt_text:
                        print("Skipping question with empty prompt")
                        continue
                    
                    prompt_number = q.get("prompt_number")
                    question_type = q.get("question_type")
                    response_type = q.get("response_type", question_type)

                    # Get required field from JSON, default to True if not specified
                    required = q.get("required", True)
                    if required is None:
                        required = True
                    
                    # Search for existing question by prompt text within this survey
                    existing_question = Question.query.filter_by(
                        prompt=prompt_text,
                        survey_id=survey.id
                    ).first()
                    
                    if existing_question:
                        # Update existing question
                        existing_question.active = q.get("active", True)
                        existing_question.question_type = question_type or "text"
                        existing_question.response_type = response_type or "text"
                        existing_question.prompt_number = prompt_number
                        existing_question.options = q.get("options")
                        existing_question.question_group_id = group.id
                        existing_question.question_metadata = q.get("metadata")
                        existing_question.required = required
                        question = existing_question
                        stats['questions_updated'] += 1
                        print(f"Updated question: {prompt_text[:50]}...")
                    else:
                        # Create new question
                        question = Question(
                            prompt=prompt_text,
                            question_type=question_type or "text",
                            response_type=response_type or "text",
                            prompt_number=prompt_number,
                            options=q.get("options"),
                            survey_id=survey.id,
                            question_group_id=group.id,
                            question_metadata=q.get("metadata"),
                            required=required,
                            active=q.get("active", True)
                        )
                        db.session.add(question)
                        stats['questions_created'] += 1
                        print(f"Created new question: {prompt_text[:50]}...")
                    
                    db.session.flush()
                    if prompt_number is not None:
                        prompt_to_qid[prompt_number] = question.id

            # Delete existing SurveyLogic for this survey before recreating
            SurveyLogic.query.filter_by(survey_id=survey.id).delete()
            db.session.flush()

            # Now create SurveyLogic entries from questions
            def create_logic_for(source, source_question_id=None, source_group_id=None):
                logic_items = source.get("survey_logic")
                if not logic_items:
                    return

                # support either list of items or single dict
                items = logic_items if isinstance(logic_items, list) else [logic_items]
                for item in items:
                    resp_id = item.get("response_option_id")
                    # DB expects a string for response_option_id
                    resp_id_str = None if resp_id is None else str(resp_id)
                    next_prompt = item.get("next_prompt_number")
                    next_qid = prompt_to_qid.get(next_prompt) if next_prompt is not None else None

                    logic = SurveyLogic(
                        survey_id=survey.id,
                        question_id=source_question_id,
                        question_group_id=source_group_id,
                        response_option_id=resp_id_str if resp_id_str is not None else "",
                        next_question_id=next_qid
                    )
                    db.session.add(logic)
                    stats['logic_rules_created'] += 1

            # Logic for groups (use the ids we recorded when creating groups)
            for g in survey_obj.get("question_groups", []):
                group_name = g.get("name")
                group_full_name = f'group_{survey.id}_{group_name}' if group_name else f"group_{survey.id}_{len(g.get('questions',[]))}"
                group_id = group_name_to_id.get(group_full_name)
                if group_id is None:
                    # fallback: try to query DB (defensive)
                    grp = QuestionGroup.query.filter_by(name=group_full_name, survey_id=survey.id).first()
                    group_id = grp.id if grp else None
                if group_id is not None:
                    create_logic_for(g, source_group_id=group_id)
                else:
                    print(f"Warning: could not determine id for group '{group_name}' to create logic; skipping group-level logic")

            # Count disabled questions (those still marked as inactive after processing)
            # Flush to ensure all changes are reflected in the query
            db.session.flush()
            disabled_questions = Question.query.filter_by(survey_id=survey.id, active=False).count()
            stats['questions_disabled'] += disabled_questions

        db.session.commit()
        
        # Print statistics summary
        print("\n" + "="*60)
        print("POPULATION STATISTICS")
        print("="*60)
        print(f"Surveys:")
        print(f"  - Created: {stats['surveys_created']}")
        print(f"  - Updated: {stats['surveys_updated']}")
        print(f"\nQuestion Groups:")
        print(f"  - Created: {stats['question_groups_created']}")
        print(f"  - Updated: {stats['question_groups_updated']}")
        print(f"\nQuestions:")
        print(f"  - Created: {stats['questions_created']}")
        print(f"  - Updated: {stats['questions_updated']}")
        print(f"  - Disabled: {stats['questions_disabled']}")
        print(f"\nSurvey Logic:")
        print(f"  - Rules Created: {stats['logic_rules_created']}")
        print("="*60)
        print("Database populated from JSON")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Populate database with questions from JSON file')
    parser.add_argument('--json-file', '-j', 
                       default='assets/surveys/english.json',
                       help='Path to JSON file containing surveys (default: assets/surveys/english.json)')
    parser.add_argument('--append', '-a', action='store_true', help='Append questions to existing ones')
    args = parser.parse_args()
    populate_questions(args.json_file, args.append)
