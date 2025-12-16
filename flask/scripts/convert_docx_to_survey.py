#!/usr/bin/env python3
"""Convert Word document themes to survey JSON format."""

import zipfile
import xml.etree.ElementTree as ET
import json
import re
import argparse
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

# Constants
MAX_THEME_LENGTH, MIN_QUESTION_LENGTH, MAX_SUBCATEGORY_LENGTH = 200, 20, 100
WORD_NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}

# Regex patterns
ARABIC = re.compile(r'[\u0600-\u06FF]')
EN_PAREN = re.compile(r'\(([^)]+)\)')
EN_PAREN_CAP = re.compile(r'\([A-Z]')
NUM_Q = re.compile(r'^(\d+)\.\s*(.+)')
LETTER_SUB = re.compile(r'^[A-Z][\.\)]\s*')
NUM = re.compile(r'^\d+\.')
SLUG = re.compile(r'[^a-zA-Z0-9]+')
UNDERSCORE = re.compile(r'_+')

# Arabic transliteration map
AR_TRANS = {
    'ا': 'a', 'أ': 'a', 'إ': 'i', 'آ': 'aa', 'ب': 'b', 'ت': 't', 'ث': 'th', 'ج': 'j',
    'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'dh', 'ر': 'r', 'ز': 'z', 'س': 's', 'ش': 'sh',
    'ص': 's', 'ض': 'd', 'ط': 't', 'ظ': 'z', 'ع': 'a', 'غ': 'gh', 'ف': 'f', 'ق': 'q',
    'ك': 'k', 'ل': 'l', 'م': 'm', 'ن': 'n', 'ه': 'h', 'و': 'w', 'ي': 'y', 'ى': 'a',
    'ة': 'a', 'ء': 'a', 'ئ': 'i', 'ؤ': 'u', ' ': '_', '-': '_'
}


def extract_text(p, ns):
    return ''.join(t.text for t in p.findall('.//w:t', ns) if t.text).strip()


def get_style(p, ns):
    pPr = p.find('w:pPr', ns)
    return pPr.find('w:pStyle', ns).get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val') if pPr is not None and pPr.find('w:pStyle', ns) is not None else None


def has_arabic(t): return bool(ARABIC.search(t)) if t else False
def has_en_paren(t): return t and '(' in t and ')' in t and bool(EN_PAREN_CAP.search(t))
def extract_en(t): return (m := EN_PAREN.search(t)) and m.group(1) or t if t else ''
def is_num_q(t): return bool(NUM_Q.match(t)) if t else False
def is_q_mark(t): return t and (t.endswith('؟') or t.endswith('?'))


def arabic_to_slug(t):
    s = ''.join(AR_TRANS.get(c, c.lower() if c.isalnum() else '_' if c in '_-' else '') for c in t)
    return UNDERSCORE.sub('_', s).strip('_')


def text_to_slug(t):
    return UNDERSCORE.sub('_', SLUG.sub('_', (t or '').lower()).strip('_'))


@dataclass
class ThemeBuilder:
    arabic_title: Optional[str] = None
    english_title: Optional[str] = None
    questions: List[str] = field(default_factory=list)
    
    def start(self, ar, en=None):
        self.arabic_title, self.english_title, self.questions = ar, en, []
    
    def add_q(self, q):
        if q: self.questions.append(q.strip())
    
    def finalize(self):
        return {'arabic_title': self.arabic_title, 'english_title': self.english_title, 'questions': self.questions.copy()} if self.arabic_title else None
    
    def active(self): return self.arabic_title is not None


def is_subcategory(t, theme):
    if not t: return False
    if LETTER_SUB.match(t): return True
    if not theme or not theme.active(): return False
    ar, en_p, num = has_arabic(t), has_en_paren(t), bool(NUM.match(t))
    return (ar and en_p and not num) or (ar and len(t) < MAX_SUBCATEGORY_LENGTH and not num and not is_q_mark(t))


def process_title(text, style, next_t, next_s, theme, themes):
    if style != 'Title': return False, 0
    if has_arabic(text):
        if theme.active(): themes.append(theme.finalize())
        en, skip = None, 1
        if next_t and (next_s == 'Title' or has_en_paren(next_t)):
            en, skip = next_t.strip(), 2
        theme.start(text, en)
        return True, skip
    if has_en_paren(text) and theme.active() and not theme.english_title:
        theme.english_title = text.strip()
        return True, 1
    return False, 0


def process_theme(text, next_t, theme, themes):
    if theme.active(): return False, 0
    if has_arabic(text) and len(text) < MAX_THEME_LENGTH and not NUM.match(text):
        if next_t and has_en_paren(next_t) and not is_subcategory(next_t, theme):
            theme.start(text, next_t.strip())
            return True, 2
    if has_en_paren(text) and len(text) < MAX_THEME_LENGTH and not NUM.match(text):
        theme.start(text, text)
        return True, 1
    return False, 0


def process_q(text, theme):
    if not theme.active() or not text: return
    if m := NUM_Q.match(text):
        theme.add_q(m.group(2))
    elif has_arabic(text) and is_q_mark(text) and not is_subcategory(text, theme) and len(text) > MIN_QUESTION_LENGTH:
        theme.add_q(text)


def parse_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read('word/document.xml'))
        paragraphs = root.findall('.//w:p', WORD_NS)
    except Exception as e:
        raise ValueError(f"Failed to read Word document: {e}")
    
    themes, theme = [], ThemeBuilder()
    i = 0
    while i < len(paragraphs):
        text = extract_text(paragraphs[i], WORD_NS)
        if not text:
            i += 1
            continue
        
        style = get_style(paragraphs[i], WORD_NS)
        next_t = extract_text(paragraphs[i+1], WORD_NS) if i+1 < len(paragraphs) else None
        next_s = get_style(paragraphs[i+1], WORD_NS) if i+1 < len(paragraphs) else None
        
        if (cont := process_title(text, style, next_t, next_s, theme, themes))[0]:
            i += cont[1]
            continue
        if is_subcategory(text, theme):
            i += 1
            continue
        if (cont := process_theme(text, next_t, theme, themes))[0]:
            i += cont[1]
            continue
        process_q(text, theme)
        i += 1
    
    if theme.active():
        themes.append(theme.finalize())
    return themes


def generate_filename(en_title): return text_to_slug(extract_en(en_title or ''))


def convert_to_json(themes):
    surveys = []
    for t in themes:
        if not t or not t.get('questions'):
            print(f"Warning: Theme '{t.get('arabic_title', 'Unknown') if t else 'Unknown'}' has no questions, skipping.", file=sys.stderr)
            continue
        filename = generate_filename(t.get('english_title') or '')
        survey = {
            "survey_name": t['arabic_title'],
            "description": t['arabic_title'],
            "consent_form": None,
            "question_groups": [{"name": "main_group", "group_type": "random", "questions": []}],
            "_filename": filename
        }
        for idx, q in enumerate(t['questions']):
            survey["question_groups"][0]["questions"].append({
                "prompt_number": idx, "prompt": q, "question_type": "audio", "options": None, "required": False
            })
        surveys.append(survey)
    return surveys


def main():
    p = argparse.ArgumentParser(description='Convert Word document themes to survey JSON format')
    p.add_argument('--input', '-i', default='Themes for Fieldwork Data Collection.10.12.2025.docx', help='Input Word document')
    p.add_argument('--output', '-o', default='flask/assets/surveys', help='Output directory or JSON file')
    p.add_argument('--single-file', action='store_true', help='Write all themes to a single JSON file')
    p.add_argument('--dry-run', action='store_true', help='Preview output without writing file')
    args = p.parse_args()
    
    root = Path(__file__).parent.parent.parent
    input_path, output_path = root / args.input, root / args.output
    
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    output_is_dir = not args.single_file and (not output_path.suffix or output_path.is_dir() or not output_path.exists())
    
    try:
        print(f"Parsing document: {input_path}")
        themes = parse_docx(str(input_path))
        print(f"Found {len(themes)} themes")
        
        if not themes:
            print("Warning: No themes found in document.", file=sys.stderr)
            sys.exit(1)
        
        surveys = convert_to_json(themes)
        print(f"Generated {len(surveys)} surveys")
        
        if not surveys:
            print("Error: No valid surveys generated.", file=sys.stderr)
            sys.exit(1)
        
        clean = lambda s: {k: v for k, v in s.items() if k != '_filename'}
        
        if args.dry_run:
            print("\n=== DRY RUN - Output preview ===")
            if args.single_file:
                print(json.dumps([clean(s) for s in surveys], ensure_ascii=False, indent=4))
            else:
                for s in surveys:
                    print(f"\n--- Survey: {s['survey_name']} (filename: {s.get('_filename', 'N/A')}.json) ---")
                    print(json.dumps([clean(s)], ensure_ascii=False, indent=4))
            print("\n=== End of preview ===")
        else:
            if args.single_file:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump([clean(s) for s in surveys], f, ensure_ascii=False, indent=4)
                print(f"\nSuccessfully wrote {len(surveys)} surveys to: {output_path}")
            else:
                output_dir = output_path if output_is_dir else output_path.parent
                output_dir.mkdir(parents=True, exist_ok=True)
                written = []
                for s in surveys:
                    fp = output_dir / f"{s.get('_filename', 'survey')}.json"
                    with open(fp, 'w', encoding='utf-8') as f:
                        json.dump([clean(s)], f, ensure_ascii=False, indent=4)
                    written.append(fp)
                print(f"\nSuccessfully wrote {len(surveys)} surveys to separate files in: {output_dir}")
                print("\nWritten files:")
                for fp in written:
                    print(f"  - {fp.name}")
            
            print("\nSurvey summary:")
            for s in surveys:
                print(f"  - {s['survey_name']}: {len(s['question_groups'][0]['questions'])} questions")
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
