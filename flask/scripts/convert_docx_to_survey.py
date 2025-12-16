#!/usr/bin/env python3
"""
Script to convert Word document themes to survey JSON format.

Converts themes from a Word document into the survey JSON format used by
the application. Each theme becomes a separate survey with a random question group.
"""

import zipfile
import xml.etree.ElementTree as ET
import json
import re
import argparse
import sys
from pathlib import Path


def extract_text_from_paragraph(paragraph, ns):
    """Extract text content from a Word paragraph element."""
    text_parts = []
    for t in paragraph.findall('.//w:t', ns):
        if t.text:
            text_parts.append(t.text)
    return ''.join(text_parts).strip()


def arabic_to_slug(arabic_text):
    """
    Convert Arabic text to a simple slug.
    Uses a basic transliteration mapping for common Arabic characters.
    """
    # Basic Arabic to English transliteration mapping
    transliteration_map = {
        'ا': 'a', 'أ': 'a', 'إ': 'i', 'آ': 'aa',
        'ب': 'b', 'ت': 't', 'ث': 'th', 'ج': 'j',
        'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'dh',
        'ر': 'r', 'ز': 'z', 'س': 's', 'ش': 'sh',
        'ص': 's', 'ض': 'd', 'ط': 't', 'ظ': 'z',
        'ع': 'a', 'غ': 'gh', 'ف': 'f', 'ق': 'q',
        'ك': 'k', 'ل': 'l', 'م': 'm', 'ن': 'n',
        'ه': 'h', 'و': 'w', 'ي': 'y', 'ى': 'a',
        'ة': 'a', 'ء': 'a', 'ئ': 'i', 'ؤ': 'u',
        ' ': '_', '-': '_'
    }
    
    slug = ''
    for char in arabic_text:
        if char in transliteration_map:
            slug += transliteration_map[char]
        elif char.isalnum():
            slug += char.lower()
        elif char in ['_', '-']:
            slug += '_'
    
    # Clean up multiple underscores
    slug = re.sub(r'_+', '_', slug)
    slug = slug.strip('_')
    
    return slug


def generate_survey_name(arabic_title, english_title):
    """
    Generate survey_name slug from Arabic and English titles.
    Format: arabic_slug_english_slug
    """
    # Extract English part from parentheses if present
    english_match = re.search(r'\(([^)]+)\)', english_title)
    if english_match:
        english_part = english_match.group(1)
    else:
        english_part = english_title
    
    # Convert English to slug
    english_slug = re.sub(r'[^a-zA-Z0-9]+', '_', english_part.lower()).strip('_')
    
    # Convert Arabic to slug
    arabic_slug = arabic_to_slug(arabic_title)
    
    # Combine
    if arabic_slug and english_slug:
        return f"{arabic_slug}_{english_slug}"
    elif arabic_slug:
        return arabic_slug
    elif english_slug:
        return english_slug
    else:
        return "survey"


def parse_docx(docx_path):
    """
    Parse Word document and extract themes with their questions.
    
    Returns a list of dictionaries, each containing:
    - arabic_title: Arabic theme title
    - english_title: English theme title
    - questions: List of question texts
    """
    try:
        z = zipfile.ZipFile(docx_path)
        doc_xml = z.read('word/document.xml')
        root = ET.fromstring(doc_xml)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paragraphs = root.findall('.//w:p', ns)
    except Exception as e:
        raise ValueError(f"Failed to read Word document: {e}")
    
    themes = []
    current_theme_ar = None
    current_theme_en = None
    current_questions = []
    
    i = 0
    while i < len(paragraphs):
        text = extract_text_from_paragraph(paragraphs[i], ns)
        
        if not text:
            i += 1
            continue
        
        # Check if this is an Arabic theme title (Arabic characters, no numbers, short)
        # This could be pure Arabic or Arabic with Arabic text in parentheses
        is_arabic_title = (
            re.search(r'[\u0600-\u06FF]', text) and  # Contains Arabic characters
            len(text) < 200 and  # Reasonable length
            not re.match(r'^\d+\.', text)  # Not a numbered question
        )
        
        if is_arabic_title:
            # Check next paragraph for English translation
            if i + 1 < len(paragraphs):
                next_text = extract_text_from_paragraph(paragraphs[i+1], ns)
                # If next paragraph has English in parentheses, combine them
                if '(' in next_text and ')' in next_text and re.search(r'\([A-Z]', next_text):
                    # Save previous theme if exists
                    if current_theme_ar is not None:
                        themes.append({
                            'arabic_title': current_theme_ar,
                            'english_title': current_theme_en,
                            'questions': current_questions
                        })
                    
                    # Start new theme
                    current_theme_ar = text
                    current_theme_en = next_text.strip()
                    current_questions = []
                    i += 2  # Skip both paragraphs
                    continue
        
        # Check if it's a standalone English theme (in case Arabic was missed)
        # This handles cases where we might have missed the Arabic title
        if '(' in text and ')' in text and re.search(r'\([A-Z]', text) and not re.match(r'^\d+\.', text) and len(text) < 200:
            # Only use if we don't already have a theme
            if current_theme_ar is None:
                current_theme_ar = text
                current_theme_en = text
                current_questions = []
            i += 1
            continue
        
        # Detect questions (start with number followed by period)
        if current_theme_ar is not None and text:
            match = re.match(r'^(\d+)\.\s*(.+)', text)
            if match:
                question_text = match.group(2).strip()
                current_questions.append(question_text)
        
        i += 1
    
    # Don't forget the last theme
    if current_theme_ar is not None:
        themes.append({
            'arabic_title': current_theme_ar,
            'english_title': current_theme_en,
            'questions': current_questions
        })
    
    return themes


def generate_filename(english_title):
    """
    Generate filename from English title.
    Extracts English text from parentheses and converts to slug.
    """
    # Extract English part from parentheses if present
    english_match = re.search(r'\(([^)]+)\)', english_title)
    if english_match:
        english_part = english_match.group(1)
    else:
        english_part = english_title
    
    # Convert to slug: lowercase, replace spaces/special chars with underscores
    filename = re.sub(r'[^a-zA-Z0-9]+', '_', english_part.lower()).strip('_')
    
    # Clean up multiple underscores
    filename = re.sub(r'_+', '_', filename)
    
    return filename


def convert_to_survey_json(themes):
    """
    Convert parsed themes to survey JSON format.
    
    Returns a list of survey objects matching the arabic.json structure.
    Each survey object includes a '_filename' key for file naming (not included in JSON output).
    """
    surveys = []
    
    for theme in themes:
        if not theme['questions']:
            print(f"Warning: Theme '{theme['arabic_title']}' has no questions, skipping.")
            continue
        
        # Use Arabic title as survey_name
        survey_name = theme['arabic_title']
        
        # Generate filename from English title
        filename = generate_filename(theme['english_title'])
        
        # Create survey object
        survey = {
            "survey_name": survey_name,
            "description": theme['arabic_title'],
            "consent_form": None,
            "question_groups": [
                {
                    "name": "main_group",
                    "group_type": "random",
                    "questions": []
                }
            ],
            "_filename": filename  # Internal field for file naming, will be removed before JSON output
        }
        
        # Add questions with zero-indexed prompt_numbers
        for idx, question_text in enumerate(theme['questions']):
            question = {
                "prompt_number": idx,
                "prompt": question_text,
                "question_type": "audio",
                "options": None,
                "required": False
            }
            survey["question_groups"][0]["questions"].append(question)
        
        surveys.append(survey)
    
    return surveys


def main():
    parser = argparse.ArgumentParser(
        description='Convert Word document themes to survey JSON format'
    )
    parser.add_argument(
        '--input', '-i',
        default='Themes for Fieldwork Data Collection.10.12.2025.docx',
        help='Path to input Word document (default: Themes for Fieldwork Data Collection.10.12.2025.docx)'
    )
    parser.add_argument(
        '--output', '-o',
        default='flask/assets/surveys',
        help='Path to output directory or JSON file (default: flask/assets/surveys). If directory, each theme will be written to a separate file.'
    )
    parser.add_argument(
        '--single-file',
        action='store_true',
        help='Write all themes to a single JSON file (default: write each theme to separate files)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview output without writing file'
    )
    
    args = parser.parse_args()
    
    # Resolve paths relative to project root
    project_root = Path(__file__).parent.parent.parent
    input_path = project_root / args.input
    output_path = project_root / args.output
    
    # Validate input file
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    # Determine if output is a directory or file
    output_is_dir = args.single_file == False and (output_path.suffix == '' or output_path.is_dir() or not output_path.exists())
    
    try:
        # Parse document
        print(f"Parsing document: {input_path}")
        themes = parse_docx(str(input_path))
        print(f"Found {len(themes)} themes")
        
        # Convert to JSON format
        surveys = convert_to_survey_json(themes)
        print(f"Generated {len(surveys)} surveys")
        
        # Helper function to remove internal fields before JSON serialization
        def clean_survey_for_json(survey):
            """Remove internal fields like _filename before JSON output."""
            cleaned = survey.copy()
            cleaned.pop('_filename', None)
            return cleaned
        
        if args.dry_run:
            print("\n=== DRY RUN - Output preview ===")
            if args.single_file:
                cleaned_surveys = [clean_survey_for_json(s) for s in surveys]
                json_output = json.dumps(cleaned_surveys, ensure_ascii=False, indent=4)
                print(json_output)
            else:
                for survey in surveys:
                    print(f"\n--- Survey: {survey['survey_name']} (filename: {survey.get('_filename', 'N/A')}.json) ---")
                    cleaned_survey = clean_survey_for_json(survey)
                    json_output = json.dumps([cleaned_survey], ensure_ascii=False, indent=4)
                    print(json_output)
            print("\n=== End of preview ===")
        else:
            if args.single_file:
                # Write all surveys to a single file
                output_path.parent.mkdir(parents=True, exist_ok=True)
                cleaned_surveys = [clean_survey_for_json(s) for s in surveys]
                json_output = json.dumps(cleaned_surveys, ensure_ascii=False, indent=4)
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(json_output)
                print(f"\nSuccessfully wrote {len(surveys)} surveys to: {output_path}")
            else:
                # Write each survey to a separate file
                output_dir = output_path if output_is_dir else output_path.parent
                output_dir.mkdir(parents=True, exist_ok=True)
                
                written_files = []
                for survey in surveys:
                    # Use English filename
                    filename = f"{survey.get('_filename', 'survey')}.json"
                    file_path = output_dir / filename
                    cleaned_survey = clean_survey_for_json(survey)
                    json_output = json.dumps([cleaned_survey], ensure_ascii=False, indent=4)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(json_output)
                    written_files.append(file_path)
                
                print(f"\nSuccessfully wrote {len(surveys)} surveys to separate files in: {output_dir}")
                print("\nWritten files:")
                for file_path in written_files:
                    print(f"  - {file_path.name}")
            
            # Print summary
            print("\nSurvey summary:")
            for survey in surveys:
                q_count = len(survey['question_groups'][0]['questions'])
                print(f"  - {survey['survey_name']}: {q_count} questions")
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

