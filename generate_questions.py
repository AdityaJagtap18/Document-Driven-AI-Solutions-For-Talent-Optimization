#!/usr/bin/env python
# coding: utf-8

import os
import json
import shutil
from pathlib import Path
from PyPDF2 import PdfReader
from langchain.chat_models import ChatOpenAI
from langchain.schema import HumanMessage

from llm_utils import call_with_retry
from secrets_loader import get_secret

# === Config ===
_openai_key = get_secret("OPENAI_API_KEY")
if not _openai_key:
    raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file (see .env.example) or configure AZURE_KEY_VAULT_URL.")
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

    prompt = f"""
You are an AI interviewer.

Generate 10 relevant and intelligent interview questions based solely on the following candidate resume:

Resume Content:
\"\"\"{resume_text}\"\"\"

Return the questions in a clean numbered list format.
"""

    try:
        print("Sending request to OpenAI...")
        llm = ChatOpenAI(temperature=0.7, max_tokens=800, model="gpt-3.5-turbo")
        response = call_with_retry(
            llm, [HumanMessage(content=prompt)],
            on_retry=lambda attempt, e: print(f"Retry {attempt} for {resume_name}: {e}"),
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
