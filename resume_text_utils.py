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


# Real, observed LLM outputs for a Name field it couldn't confidently
# extract -- confirmed by inspection of real merge-extraction responses
# in logs/llm_response_cache.json, not a hypothetical list. The model
# answering "Not Provided" (or similar) is a real, valid refusal, but
# previously slipped past ResumeParser.py's own fallback-trigger check
# (which only caught an EMPTY string, "candidate" in the text, or a
# literal "<placeholder>" token) because these phrases are non-empty,
# don't mention "candidate", and don't start with "<" -- a real,
# confirmed bug, not a hypothetical edge case.
_PLACEHOLDER_NAMES = {
    "not provided", "not specified", "n/a", "na", "unknown", "none",
    "unspecified", "not available", "not mentioned", "not found",
}


def is_placeholder_name(name: str) -> bool:
    """True when `name` is one of the real placeholder phrases a model
    has been observed to answer instead of a genuine name -- case- and
    punctuation-insensitive (".", so "N/A." still matches)."""
    if not isinstance(name, str):
        return False
    normalized = name.strip().strip(".").lower()
    return normalized in _PLACEHOLDER_NAMES


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
