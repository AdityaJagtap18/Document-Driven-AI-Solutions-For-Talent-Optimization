"""F-16 (Phase 1 Implementation Contract, Task 7): a minimal, honestly
scoped fairness guard.

This is a STATIC STRUCTURAL guard, not a statistical fairness measurement
and not bias mitigation. It answers exactly one narrow question: could a
protected-attribute-shaped field reach resume_scoring.py's scoring
functions, or does one already exist in the real candidate document
shape? It does NOT implement any of the 5 proxy-bias findings already on
file in docs/fde-process/05-bias-fairness-audit.md (Employment Gaps
penalty, Culture Fit Assessment, Industry Penalty, Effort Needed,
Candidate Type Score) -- those are proxy-bias risks in otherwise-
legitimate-looking fields, a different and larger question than this
guard answers, and they remain pending the user's own sign-off per that
document's closing line. This module/guard must never be described as
"bias mitigation" or a "fairness certification" in any report or demo.

Scope: this checks resume_scoring.py specifically, where the real scoring
math lives (ResumeMEtrics.py mostly just calls these functions and writes
CSV columns -- it was not re-audited field-by-field here, a documented
limitation, not a silent gap).
"""

import re

# Deliberately conservative, real protected-category terms (the
# categories employment-discrimination law in most jurisdictions
# recognizes), not every word that could coincidentally share a
# substring -- word-boundary matched, case-insensitive, against FIELD
# NAMES (e.g. "Gender", "Date of Birth"), never against free-text field
# VALUES (a Profile Summary mentioning someone's age in prose is not what
# this guard checks).
PROTECTED_ATTRIBUTE_PATTERN = re.compile(
    r"(?i)\b("
    r"race|ethnic(?:ity)?|national(?:ity)?\s*origin|nationality|citizenship|"
    r"gender|\bsex\b|sexual\s*orientation|"
    r"age|date\s*of\s*birth|\bdob\b|"
    r"relig\w*|creed|"
    r"disability|disabled|"
    r"marital\s*status|pregnan\w*|"
    r"caste|skin\s*colo[u]?r|veteran\s*status"
    r")\b"
)


def find_protected_attribute_fields(field_names: list) -> list:
    """Returns the subset of `field_names` whose name itself looks like a
    protected-attribute field, per PROTECTED_ATTRIBUTE_PATTERN. Empty
    list = clean. Never inspects field VALUES, only names."""
    return [f for f in field_names if f and PROTECTED_ATTRIBUTE_PATTERN.search(f)]


def extract_json_like_keys(template_str: str) -> list:
    """Regex-extracts `"Key Name":` style keys from a JSON-shaped prompt
    template string (e.g. rag_config.DEFAULT_MERGE_EXTRACTION_TEMPLATE) --
    not a real JSON parse, since these templates contain non-JSON
    placeholder tokens like bare `true` or `<...>` descriptions, not
    actual values."""
    return re.findall(r'"([^"]+)"\s*:', template_str)


def extract_row_subscript_fields(source_code: str) -> list:
    """Regex-extracts every field name resume_scoring.py's functions read
    off a `row` dict/Series via row["..."]/row['...']/row.get("...").
    Static source inspection, not execution -- this is what the guard
    actually verifies against, not an assumption about what the
    functions "probably" read."""
    patterns = (
        r'row\[\s*["\']([^"\']+)["\']\s*\]',
        r'row\.get\(\s*["\']([^"\']+)["\']',
    )
    fields = []
    for pattern in patterns:
        fields.extend(re.findall(pattern, source_code))
    return fields
