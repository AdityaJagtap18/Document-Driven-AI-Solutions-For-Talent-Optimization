#!/usr/bin/env python
# coding: utf-8

import os
import json
import shutil
from pathlib import Path
from PyPDF2 import PdfReader
from langchain.schema import HumanMessage

from llm_utils import call_with_retry, get_chat_llm, using_azure_openai
from rag_config import sanitize_for_prompt
from secrets_loader import get_secret

# === Config ===
if not using_azure_openai():
    _openai_key = get_secret("OPENAI_API_KEY")
    if not _openai_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file (see .env.example), configure AZURE_KEY_VAULT_URL, or set AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_API_KEY/AZURE_OPENAI_CHAT_DEPLOYMENT to use Azure OpenAI instead.")
    os.environ["OPENAI_API_KEY"] = _openai_key
parsed_folder = Path("ParsedReadyForQuestions")
output_folder = Path("OutputResume_Folder")
interview_data_root = Path("InterviewData")

def extract_text_from_pdf(pdf_path):
    try:
        reader = PdfReader(str(pdf_path))
        text = ""
        for page in reader.pages:
            if page.extract_text():
                text += page.extract_text()
        return text.strip()
    except Exception as e:
        print(f"ERROR: Failed to extract text from {pdf_path.name} - {e}")
        return ""

def build_questions_prompt(resume_text):
    """Prompt text is untrusted -- it comes straight from the candidate's
    own PDF, re-extracted independently of ResumeParser.py's (sanitized)
    pipeline -- so it's sanitized here too before interpolation (see
    rag_config.py's docstring for exactly what this does and doesn't
    protect against)."""
    return f"""
You are an AI interviewer.

Generate 10 relevant and intelligent interview questions based solely on the following candidate resume:

Resume Content:
\"\"\"{sanitize_for_prompt(resume_text)}\"\"\"

Return the questions in a clean numbered list format.
"""


_MIN_QUESTION_CHARS = 8  # drops leftover markdown/heading artifacts, not real questions
_MAX_QUESTIONS_RETURNED = 15  # caps a runaway/malformed LLM response rather than returning it as-is

# Candidate fields used as the "resume" side of the grounding -- the
# already-extracted, already-verified structured profile ResumeParser.py's
# real pipeline produced, NOT the raw original PDF text. See
# build_grounded_questions_prompt()'s docstring for why: candidate.jd_id
# is declared in the schema but never populated by the real ingestion
# pipeline, and raw resume text isn't persisted anywhere queryable by
# candidate_id either (only the Blob path is) -- re-downloading and
# re-extracting the original PDF for every question-generation call would
# be a materially bigger integration than this task's smallest-safe-slice
# scope. This profile is the same real, already-verified data
# chat_guardrails.py's F-08 endpoint already grounds its answers on.
_RESUME_CONTEXT_FIELDS = (
    "Name", "Profile Summary", "Top Matching Keywords", "Missing Keywords",
    "Candidate Type", "Resume Strength Score", "JD Match",
)


def build_resume_context(candidate: dict) -> dict:
    """The resume-side grounding for build_grounded_questions_prompt() --
    pulled from the candidate's already-persisted, already-verified
    structured profile (see _RESUME_CONTEXT_FIELDS's docstring above)."""
    return {field: candidate.get(field) for field in _RESUME_CONTEXT_FIELDS}


def build_grounded_questions_prompt(resume_context: dict, jd_text: str) -> str:
    """The integrated (candidate_id + jd_id) counterpart to
    build_questions_prompt()'s original file-based prompt. Both the
    candidate's resume-derived context AND the job description text are
    untrusted input -- a JD is staff-authored, not candidate-controlled,
    but it's still free text that ends up interpolated into an LLM
    prompt, so it's sanitized here too, same discipline and same
    function as every other untrusted-text call site in this repo (see
    rag_config.py's docstring)."""
    sanitized_context = {
        key: (sanitize_for_prompt(value) if isinstance(value, str) else value)
        for key, value in resume_context.items()
    }
    sanitized_jd = sanitize_for_prompt(jd_text)
    return f"""
You are an AI interviewer preparing questions for a specific candidate
being considered for a specific role.

Generate 10 relevant and intelligent interview questions grounded in
BOTH the candidate's resume profile below AND the job description below.
Prefer questions that probe real gaps between the two (see "Missing
Keywords") over generic questions either could answer.

Candidate resume profile:
\"\"\"{sanitized_context}\"\"\"

Job description:
\"\"\"{sanitized_jd}\"\"\"

Return the questions in a clean numbered list format.
"""


def parse_and_validate_questions(raw_text: str) -> list:
    """Turns the LLM's free-text response into a validated list of
    question strings, or raises ValueError if the response doesn't look
    like a real set of questions -- e.g. an empty/garbled response, or
    one that's entirely markdown artifacts/blank lines. Callers map
    ValueError to a clean error response rather than silently returning
    garbage as if it were a successful generation. Caps the result at
    _MAX_QUESTIONS_RETURNED so a runaway response (e.g. the model
    ignoring "Return ... 10 ...") doesn't pass through unbounded.

    Real failure mode found via a live end-to-end verification of this
    task: the model sometimes prefixes its numbered list with a markdown
    heading line (e.g. "### Interview Questions:"), which survived the
    original strip-chars set untouched and leaked through as if it were
    question #1. Dropped here: a cleaned line that ends with ":" has no
    actual question content after the label, unlike every real question
    in this model's own output style (e.g. "Topic**: actual question
    text..."), where the colon is always followed by more of the line,
    not the end of it."""
    lines = [q.strip("#0123456789.()-* ").strip() for q in raw_text.split("\n")]
    questions = [q for q in lines if len(q) >= _MIN_QUESTION_CHARS and not q.endswith(":")]
    if not questions:
        raise ValueError("LLM response did not contain any usable interview questions.")
    return questions[:_MAX_QUESTIONS_RETURNED]


def generate_questions_for_resume(resume_path_str):
    resume_path = Path(resume_path_str)

    if not resume_path.exists() or resume_path.suffix.lower() != ".pdf":
        print(f"ERROR: Invalid file - {resume_path}")
        return

    resume_name = resume_path.name

    # Extract "Firstname_Lastname" from "firstname_lastname_resume.pdf"
    name_parts = resume_path.stem.split("_")
    if len(name_parts) >= 2:
        first = name_parts[0].capitalize()
        last = name_parts[1].capitalize()
        candidate_folder_name = f"{first}_{last}"
        candidate_name = f"{first} {last}"
    else:
        candidate_folder_name = resume_path.stem.title().replace(" ", "_")
        candidate_name = resume_path.stem.title().replace("_", " ")

    resume_text = extract_text_from_pdf(resume_path)
    if not resume_text:
        print("ERROR: No text found in resume.")
        return

    prompt = build_questions_prompt(resume_text)

    try:
        print("Sending request to OpenAI...")
        llm = get_chat_llm(temperature=0.7, max_tokens=800, model="gpt-3.5-turbo")
        response = call_with_retry(
            llm, [HumanMessage(content=prompt)],
            on_retry=lambda attempt, e: print(f"Retry {attempt} for {resume_name}: {e}"),
            label="interview_questions_generation", model="gpt-3.5-turbo",
        )
        questions_raw = response.content

        questions = [q.strip("0123456789. ").strip() for q in questions_raw.split("\n") if q.strip()]

        candidate_folder = interview_data_root / candidate_folder_name
        candidate_folder.mkdir(parents=True, exist_ok=True)

        with open(candidate_folder / "interview_questions.json", "w", encoding="utf-8") as f:
            json.dump({
                "candidate_name": candidate_name,
                "questions": questions
            }, f, indent=4)

        print(f"Saved interview questions for {candidate_name}")
        shutil.move(str(resume_path), str(output_folder / resume_name))
        print("Moved processed resume.")

    except Exception as e:
        print(f"ERROR: OpenAI API failed - {e}")
