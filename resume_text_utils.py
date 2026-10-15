"""Pure text-extraction helpers used by ResumeParser.py, split out into their
own module so they're importable (and unit-testable) without pulling in
ResumeParser.py's module-level side effects (API key check, log file
creation, semantic-library imports)."""

import re
from pathlib import Path


def extract_name_from_filename(filename):
    base = Path(filename).stem.lower()
    parts = base.replace("resume", "").replace("cv", "").replace("-", " ").replace("_", " ").split()
    clean_name = " ".join(word.capitalize() for word in parts if word not in ["resume", "cv", "final", "updated"])
    return clean_name


def extract_contact_details(text):
    if not isinstance(text, str):
        return '', '', ''

    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    email_matches = re.findall(email_pattern, text)
    email = email_matches[0] if email_matches else ''

    phone_pattern = r'(?:\+91[\-\s]?|91[\-\s]?|0)?(?:[6-9]\d{2}[\s-]?\d{3}[\s-]?\d{4})'
    phone_matches = re.findall(phone_pattern, text)
    phone = phone_matches[0].replace(" ", "").replace("-", "") if phone_matches else ''

    linkedin_pattern = r'(https?://)?(www\.)?linkedin\.com/(in|pub)/[a-zA-Z0-9\-_/]+'
    linkedin_match = re.search(linkedin_pattern, text)
    linkedin = linkedin_match.group(0) if linkedin_match else ''

    return email, phone, linkedin
