#!/usr/bin/env python
# coding: utf-8

# In[1]:


import os
os.environ["OPENAI_API_KEY"] ="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"


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
import pandas as pd
from pathlib import Path
from PyPDF2 import PdfReader
from langchain.chat_models import ChatOpenAI
from langchain.prompts import PromptTemplate
import re

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

def extract_name_from_filename(filename):
    base = Path(filename).stem.lower()
    parts = base.replace("resume", "").replace("cv", "").replace("-", " ").replace("_", " ").split()
    clean_name = " ".join(word.capitalize() for word in parts if word not in ["resume", "cv", "final", "updated"])
    return clean_name

def extract_text_from_pdf(pdf_path):
    text = ""
    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        if page.extract_text():
            text += page.extract_text()
    return text

def chunk_text(text, max_tokens=3000):
    sentences = text.split('. ')
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) < max_tokens:
            current_chunk += sentence + ". "
        else:
            chunks.append(current_chunk.strip())
            current_chunk = sentence + ". "
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

def summarize_pdf(pdf_path, jd):
    resume_text = extract_text_from_pdf(pdf_path)

    # Extract email, phone, LinkedIn
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

    email, phone, linkedin = extract_contact_details(resume_text)
    chunks = chunk_text(resume_text)
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
        partial_summary = llm.predict(prompt)
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
    llm_merge = ChatOpenAI(temperature=0.3, max_tokens=1500, model="gpt-3.5-turbo")
    final_response = llm_merge.predict(merge_prompt)

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
        parsed_data = summarize_pdf(pdf_path, jd)
        parsed_data["resume_name"] = filename
        all_data.append(parsed_data)
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




