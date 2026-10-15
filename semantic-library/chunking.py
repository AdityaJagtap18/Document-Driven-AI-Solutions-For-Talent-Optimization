"""Resume-aware chunking.

chunk_text() is the original naive char-based chunker, same approach as
ResumeParser.py's chunk_text() (max N chars, split on ". "), duplicated
intentionally — this library is standalone for now, not importing from the
root scripts.

chunk_resume_text() is the fix for the chunk-quality gap found when this
library was first tested against real resumes: chunk 0 of nearly every
resume was dominated by name/contact/education header noise, since naive
char-chunking has no concept of resume structure. It splits on common resume
section headers (EDUCATION, EXPERIENCE, SKILLS, PROJECTS, ...) instead, so
each chunk carries one coherent section, and strips contact-info noise
(email, linkedin/github URLs, labeled Name:/Email:/Phone:/Address: lines)
from the leading block before any indexing happens. Falls back to
chunk_text() if no recognizable section headers are found at all.
"""

import re


def chunk_text(text: str, max_chars: int = 3000) -> list:
    sentences = text.split(". ")
    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) < max_chars:
            current += sentence + ". "
        else:
            if current.strip():
                chunks.append(current.strip())
            current = sentence + ". "
    if current.strip():
        chunks.append(current.strip())
    return chunks


EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin|github)\.com/\S+", re.IGNORECASE)
LABELED_CONTACT_RE = re.compile(r"(?mi)^[ \t]*(name|email|phone|address|contact)\s*:\s*.*$")

# Common resume section headers. Sorted longest-first so e.g. "technical
# skills" is tried before the more general "skills".
_SECTION_HEADERS = sorted(
    [
        "career objective", "objective", "professional summary", "executive summary",
        "summary", "profile",
        "experience summary", "professional experience", "work experience",
        "employment history", "experience",
        "education", "academic qualifications", "academics", "academic",
        "skills", "technical skills", "core competencies", "key skills",
        "projects", "academic projects", "personal projects",
        "certifications & courses", "certifications and courses", "certifications",
        "achievements", "awards", "publications",
        "languages", "declaration", "personal details", "hobbies", "interests",
        "extracurricular", "responsibilities",
    ],
    key=len,
    reverse=True,
)

# A header must occupy its own line (optionally with a leading bullet and/or
# a trailing colon) — this is what stops "experience" matching mid-sentence
# inside "2.5 years of working experience in ...".
SECTION_HEADER_RE = re.compile(
    r"(?mi)^[ \t]*[-*•]?[ \t]*(" + "|".join(re.escape(h) for h in _SECTION_HEADERS) + r")[ \t]*:?[ \t]*$"
)


def _strip_contact_noise(text: str) -> str:
    text = EMAIL_RE.sub("", text)
    text = URL_RE.sub("", text)
    text = LABELED_CONTACT_RE.sub("", text)
    return text


def chunk_resume_text(text: str, max_chars: int = 1500) -> list:
    matches = list(SECTION_HEADER_RE.finditer(text))

    if not matches:
        return chunk_text(_strip_contact_noise(text), max_chars=max_chars)

    chunks = []

    # Block before the first recognized section header is usually just name/
    # contact info; keep it only if there's substantive content left after
    # stripping contact noise (e.g. a short unlabeled intro blurb).
    lead = _strip_contact_noise(text[: matches[0].start()]).strip()
    if len(lead) > 20:
        chunks.append(lead)

    for i, m in enumerate(matches):
        header = m.group(1).strip()
        section_start = m.end()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_text = text[section_start:section_end].strip()
        if not section_text:
            continue
        combined = f"{header}: {section_text}"
        if len(combined) <= max_chars:
            chunks.append(combined)
        else:
            # Long section (e.g. multi-role Experience) — sub-chunk, keeping
            # the header on each piece so retrieval still knows the section.
            for sub in chunk_text(section_text, max_chars=max_chars):
                chunks.append(f"{header}: {sub}")

    return chunks if chunks else chunk_text(_strip_contact_noise(text), max_chars=max_chars)
