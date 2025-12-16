#!/usr/bin/env python3
"""
Script to populate the database with questions from JSON file
"""
import os
import sys
import json

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

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

    The JSON is expected to be a list of survey objects. For each survey we create
    a Survey, its Questions, QuestionGroups and SurveyLogic rows. If append is False
    and a survey with the same name exists, it will be removed and recreated.
    """
    app = create_app()

    with app.app_context():
        surveys_data = load_json(json_file)

        if not surveys_data:
            print("No surveys to add. Exiting.")
            return

        for survey_obj in surveys_data:
            survey_name = survey_obj.get("survey_name")
            if not survey_name:
                print("Skipping survey with no `survey_name`")
                continue

            # If not appending, remove existing survey with same name (cascade will remove children)
            existing = Survey.query.filter_by(name=survey_name).first()
            if existing and not append:
                print(f"Removing existing survey '{survey_name}'")
                db.session.delete(existing)
                db.session.flush()

            # Create survey
            survey = Survey(
                name=survey_name,
                description=survey_obj.get("description"),
                consent_form=survey_obj.get("consent_form")
            )
            db.session.add(survey)
            db.session.flush()  # get survey.id

            # We'll map prompt_number -> question.id for linking logic
            prompt_to_qid = {}
            # Map question group name -> id for linking group-level logic
            group_name_to_id = {}

            # NOTE: This survey format places all questions inside `question_groups`.
            # Support for top-level `questions` is intentionally omitted because the
            # provided JSON (assets/surveys/english.json) does not use it. If a survey
            # does include top-level questions in future, they will be ignored by this
            # script unless this section is re-added.

            # Next, create question groups and their questions
            for g in survey_obj.get("question_groups", []):
                group = QuestionGroup(
                    name=f'group_{survey.id}_{g.get("name")}' or f"group_{survey.id}_{len(g.get('questions',[]))}",
                    description=g.get("description"),
                    group_type=g.get("group_type", "sequential"),
                    prompt_number=g.get("prompt_number"),
                    survey_id=survey.id
                )
                db.session.add(group)
                db.session.flush()
                # record group id for later linking when creating SurveyLogic
                group_name_to_id[group.name] = group.id

                for q in g.get("questions", []):
                    prompt_number = q.get("prompt_number")
                    question_type = q.get("question_type")
                    response_type = q.get("response_type", question_type)

                    # Get required field from JSON, default to True if not specified
                    required = q.get("required", True)
                    if required is None:
                        required = True
                    
                    question = Question(
                        prompt=q.get("prompt", ""),
                        question_type=question_type or "text",
                        response_type=response_type or "text",
                        prompt_number=prompt_number,
                        options=q.get("options"),
                        survey_id=survey.id,
                        question_group_id=group.id,
                        question_metadata=q.get("metadata"),
                        required=required
                    )
                    db.session.add(question)
                    db.session.flush()
                    if prompt_number is not None:
                        prompt_to_qid[prompt_number] = question.id

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

            # There are no top-level questions in this survey format; all logic is
            # defined at the question-group level. We create group-level logic below.

            # Logic for groups (use the ids we recorded when creating groups)
            for g in survey_obj.get("question_groups", []):
                group_name = g.get("name")
                group_id = group_name_to_id.get(group_name)
                if group_id is None:
                    # fallback: try to query DB (defensive)
                    grp = QuestionGroup.query.filter_by(name=group_name, survey_id=survey.id).first()
                    group_id = grp.id if grp else None
                if group_id is not None:
                    create_logic_for(g, source_group_id=group_id)
                else:
                    print(f"Warning: could not determine id for group '{group_name}' to create logic; skipping group-level logic")

        db.session.commit()
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
