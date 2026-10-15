#!/usr/bin/env python
# coding: utf-8

# In[1]:


import os
from secrets_loader import get_secret

# get_secret checks Azure Key Vault first (if AZURE_KEY_VAULT_URL is set),
# then falls back to .env — see docs/azure-provisioning/phase2-cosmos-storage-keyvault.md.
# Written back into os.environ so langchain's ChatOpenAI (which reads
# OPENAI_API_KEY from the environment implicitly) picks it up either way.
_openai_key = get_secret("OPENAI_API_KEY")
if not _openai_key:
    raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file (see .env.example) or configure AZURE_KEY_VAULT_URL.")
os.environ["OPENAI_API_KEY"] = _openai_key


# In[3]:


from datetime import datetime
from pathlib import Path

# === Timestamp Formatter ===
def timestamp():
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

# === Log Folder and File Path ===
log_folder = Path("logs")
log_folder.mkdir(parents=True, exist_ok=True)

timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
log_path = log_folder / f"log_{timestamp_str}.txt"

# === Start of the log ===
with open(log_path, "w", encoding="utf-8") as f:
    f.write(f"{timestamp()} BE Project — Resume Parsing Started\n")

# === Log Writer Function (status-based) ===
def write_log(filename, status, jd_match=None, error=None, details=None, final=False):
    with open(log_path, "a", encoding="utf-8") as f:
        if status == "START":
            f.write(f"{timestamp()} Parsing Started: {filename}\n")
        elif status == "DETAILS":
            f.write(f"{timestamp()} Resume Summary: {details}\n")
        elif status == "END":
            f.write(f"{timestamp()} Parsing Completed: {filename}\n")
        elif status == "FAILED":
            f.write(f"{timestamp()} Failed to Parse: {filename} | Error: {error}\n")
        elif status == "FINAL" or final:
            f.write(f"{timestamp()} All Resumes Processed — Parsing Ended\n")

def write_log(*args, **kwargs):
    timestamp_str = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    with open(log_path, "a", encoding="utf-8") as log_file:
        # If just a plain message is passed
        if len(args) == 1 and not kwargs:
            log_file.write(f"{timestamp_str} {args[0]}\n")
        else:
            filename = args[0] if len(args) > 0 else "Unknown File"
            status = args[1] if len(args) > 1 else "LOG"
            jd_match = kwargs.get("jd_match")
            error = kwargs.get("error")
            details = kwargs.get("details")
            final = kwargs.get("final", False)

            if status == "START":
                log_file.write(f"{timestamp_str} Parsing Started: {filename}\n")
            elif status == "DETAILS":
                log_file.write(f"{timestamp_str} Resume Summary: {details}\n")
            elif status == "END":
                log_file.write(f"{timestamp_str} Parsing Completed: {filename}\n")
            elif status == "FAILED":
                log_file.write(f"{timestamp_str} Failed to Parse: {filename} | Error: {error}\n")
            elif status == "FINAL" or final:
                log_file.write(f"{timestamp_str} All Resumes Processed — Parsing Ended\n")
            else:
                log_file.write(f"{timestamp_str} {status}: {filename}\n")



# In[4]:


import os
import shutil
import time
import json
import sys
import pandas as pd
from pathlib import Path
from PyPDF2 import PdfReader
from langchain.chat_models import ChatOpenAI
from langchain.prompts import PromptTemplate
from resume_text_utils import extract_name_from_filename, extract_contact_details
from llm_utils import call_with_retry

# === Semantic search wiring (semantic-library/) ===
# Section-aware chunking + JD-relevance ranking, replacing the old naive
# char-based chunk_text(). Every chunk (not just the top-K sent to the LLM
# below) is still persisted into the shared semantic index, so search stays
# complete even though summarization is filtered for cost. See
# semantic-library/README.md for the measured chunking-quality fix this
# builds on (0.39-0.49 -> 0.55-0.60 cosine similarity).
_semantic_lib_dir = Path(__file__).resolve().parent / "semantic-library"
sys.path.insert(0, str(_semantic_lib_dir))
from embeddings import embed_text  # noqa: E402
from vector_store import LocalVectorStore  # noqa: E402
from index_resume import embed_and_index_resume_text, rank_by_relevance  # noqa: E402

SEMANTIC_INDEX_PATH = _semantic_lib_dir / "index" / "resume-chunks.json"
TOP_K_CHUNKS = 5  # only the top-K JD-relevant chunks go through per-chunk LLM summarization

# === Context lake dual-write (context-lake/) ===
# Best-effort: every parsed candidate is also upserted into Cosmos DB
# alongside the existing CSV write, so the lake fills up automatically from
# today's real pipeline runs instead of needing a separate backfill forever.
# Fails soft — if Cosmos DB isn't provisioned/configured, this is caught and
# logged, and the CSV write (the only write this script has ever had) is
# completely unaffected. See context-lake/README.md and
# docs/azure-provisioning/phase2-cosmos-storage-keyvault.md.
_context_lake_dir = Path(__file__).resolve().parent / "context-lake"
sys.path.insert(0, str(_context_lake_dir))
from candidate_id import candidate_id_for  # noqa: E402
from repositories import upsert_candidate  # noqa: E402

# === Configuration ===
jd_folder = Path("JD_Folder")
input_folder = Path("InputResume_Folder")
parsed_folder = Path("ParsedReadyForQuestions")
output_folder = Path("OutputResume_Folder")
csv_output_folder = Path("Resume_Parsed_CSVs")
csv_path = csv_output_folder / "resume_summary.csv"

parsed_folder.mkdir(parents=True, exist_ok=True)
output_folder.mkdir(parents=True, exist_ok=True)
csv_output_folder.mkdir(parents=True, exist_ok=True)

def write_log(msg):
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")

def extract_text_from_pdf(pdf_path):
    text = ""
    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        if page.extract_text():
            text += page.extract_text()
    return text

def summarize_pdf(pdf_path, jd, jd_vector, semantic_store):
    resume_text = extract_text_from_pdf(pdf_path)
    email, phone, linkedin = extract_contact_details(resume_text)

    # Section-aware chunk + embed the whole resume, and persist every chunk
    # to the semantic index (search stays complete even though only the
    # top-K most JD-relevant chunks get summarized below).
    all_chunk_results = embed_and_index_resume_text(pdf_path.name, resume_text, semantic_store)
    selected = rank_by_relevance(all_chunk_results, jd_vector, top_k=TOP_K_CHUNKS)
    write_log(
        f"Semantic ranking for {pdf_path.name}: using {len(selected)}/{len(all_chunk_results)} chunks "
        f"for summarization (all {len(all_chunk_results)} persisted to the semantic index)."
    )
    chunks = [r["text"] for r in selected]

    summaries = []

    llm = ChatOpenAI(temperature=0.7, max_tokens=1000, model="gpt-3.5-turbo")
    for chunk in chunks:
        prompt_template = PromptTemplate(
            input_variables=["text", "jd"],
            template="""
You are an AI assistant. Summarize the relevant resume details below in relation to the following job description.

Resume Chunk:
\"\"\"{text}\"\"\"

Job Description:
\"\"\"{jd}\"\"\"

Extract the key points in concise bullet form, JSON-style if possible. Do NOT return full JSON yet.
"""
        )
        prompt = prompt_template.format(text=chunk, jd=jd)
        partial_summary = call_with_retry(
            llm.predict, prompt,
            on_retry=lambda attempt, e: write_log(f"Retry {attempt} for chunk summarization on {pdf_path.name}: {e}"),
        )
        summaries.append(partial_summary)

    merge_prompt = f"""
You are an expert resume evaluator.

Below are summaries of different chunks of a resume, all based on the same candidate:

\"\"\"{''.join(summaries)}\"\"\"

Job Description:
\"\"\"{jd}\"\"\"

Now based on the summaries and the JD, extract the following structured JSON. 

Return clean JSON output with this format:
{{
    "Name": "<Extracted full name of the candidate>",
    "JD Match": "<% Match>",
    "Missing Keywords": {{
        "Technical Skills": [],
        "Tools & Technologies": [],
        "Concepts & Methodologies": []
    }},
    "Top Matching Keywords": [],
    "Profile Summary": "<Brief summary related to JD>",
    "Projects": [
        {{
            "Project Name": "<Title>",
            "Relevance to JD": "<High/Medium/Low>",
            "Technologies Used": [],
            "Impact": "<Project outcomes>"
        }}
    ],
    "Certifications & Courses": ["<Relevant Certifications>"],
    "Skills That Will Contribute to the Company": [],
    "Soft Skills & Leadership Qualities": ["<Communication, Leadership, etc.>"],
    "Industry Experience": "<Relevant industries like Finance, Healthcare>",
    "Culture Fit Assessment": "<High/Medium/Low – Explanation>",
    "Potential Concerns": ["<Gaps, missing skills, weaknesses>"],
    "Red Flags & Risk Analysis": ["<Major issues>"],
    "Candidate’s Growth Potential": "<How much they can grow in the company>",
    "Effort Needed by the Company": "<Low/Medium/High – Explanation>",
    "Resume Strength Score": "<Numeric score between 0.0 and 10.0>",
    "Relevant Experience (yrs)": "<Years of directly relevant experience>",
    "Employment Gaps Detected": true,
    "Resume Format Quality": "<Good/Average/Poor>",
    "Candidate Type": "<Junior/Mid-Level/Senior>",
    "HR Notes": "<Any special observations for HR>"
}}

Return only the JSON.
"""
    llm_merge = ChatOpenAI(
        temperature=0.3, max_tokens=1500, model="gpt-3.5-turbo",
        model_kwargs={"response_format": {"type": "json_object"}},
    )
    final_response = call_with_retry(
        llm_merge.predict, merge_prompt,
        on_retry=lambda attempt, e: write_log(f"Retry {attempt} for merge step on {pdf_path.name}: {e}"),
    )

    try:
        structured_data = json.loads(final_response)
        gpt_name = structured_data.get("Name", "").strip()
        fallback_name = extract_name_from_filename(pdf_path.name)

        if not gpt_name or "candidate" in gpt_name.lower() or gpt_name.startswith("<"):
            print(f"[Fallback] Using filename-based name: {fallback_name}")
            structured_data["Name"] = fallback_name

    except json.JSONDecodeError:
        print(f"Failed to parse JSON for {pdf_path.name}")
        structured_data = {"Name": extract_name_from_filename(pdf_path.name)}

    structured_data["Email"] = email
    structured_data["Phone"] = phone
    structured_data["LinkedIn"] = linkedin

    return structured_data

# === Load JD ===
jd_files = list(jd_folder.glob("*.txt"))
if not jd_files:
    print("No JD file found. Please add a .txt JD in JD_Folder.")
    exit()

latest_jd_file = max(jd_files, key=lambda f: f.stat().st_mtime)
with open(latest_jd_file, "r", encoding="utf-8") as f:
    jd = f.read()
print(f"Loaded JD from: {latest_jd_file.name}")

# Embedded once per run (not once per resume) — the JD doesn't change across
# the batch, so this is both a cost and a latency win over the naive approach.
jd_vector = embed_text(jd)
semantic_store = LocalVectorStore(str(SEMANTIC_INDEX_PATH))

# === Parse Resumes ===
pdf_paths = list(input_folder.glob("*.pdf"))
if not pdf_paths:
    print("No resumes found in the input folder.")
    exit()

all_data = []
write_log("BE Project Resume Parsing Start")

for pdf_path in pdf_paths:
    filename = pdf_path.name
    print(f"Processing: {filename}")
    write_log(f"Parsing started: {filename}")

    try:
        parsed_data = summarize_pdf(pdf_path, jd, jd_vector, semantic_store)
        parsed_data["resume_name"] = filename
        all_data.append(parsed_data)
        semantic_store.save()  # persisted per-resume so a later crash doesn't lose earlier progress

        try:
            lake_doc = dict(parsed_data)
            lake_doc["candidate_id"] = candidate_id_for(filename)
            lake_doc["source_blob_path"] = f"raw-resumes/{filename}"
            upsert_candidate(lake_doc)
            write_log(f"Context lake: wrote candidate document for {filename}")
        except Exception as e:
            write_log(f"Context lake write skipped for {filename} (CSV write unaffected): {e}")

        write_log(f"Details: {json.dumps(parsed_data, indent=2)}")
        write_log(f"Completed: {filename}")
    except Exception as e:
        write_log(f"Failed: {filename} | Error: {str(e)}")
        continue

    
    
    shutil.move(str(pdf_path), str(parsed_folder / filename))
    print(f"Moved {filename} to ParsedReadyForQuestions\n")

    

# === Save Final CSV ===
df = pd.DataFrame(all_data)

# Move contact fields before resume_name
cols = df.columns.tolist()
for field in ['LinkedIn', 'Phone', 'Email']:
    if field in cols and 'resume_name' in cols:
        f_idx = cols.index(field)
        r_idx = cols.index('resume_name')
        cols.insert(r_idx, cols.pop(f_idx))
df = df[cols]

try:
    if csv_path.exists() and os.path.getsize(csv_path) > 0:
        existing_df = pd.read_csv(csv_path)
        combined_df = pd.concat([existing_df, df], ignore_index=True)
    else:
        combined_df = df
except pd.errors.EmptyDataError:
    print("Existing CSV is empty or corrupt. Starting fresh.")
    combined_df = df

combined_df.to_csv(csv_path, index=False)
print(f"Resume data saved to: {csv_path.resolve()}")


# In[ ]:





# In[ ]:





# In[ ]:




