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
from resume_text_utils import extract_name_from_filename, extract_contact_details
from llm_utils import call_with_retry, get_chat_llm
import observability

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

# === RAG Configuration Console wiring (rag_config.py) ===
# Loaded ONCE per script run (not per resume) -- chunking.max_chars,
# retrieval.top_k/similarity_threshold, and generation params/prompt
# templates all come from here now instead of being hardcoded inline.
# Fails soft to rag_config.DEFAULT_CONFIG (== today's long-standing
# hardcoded values) if Cosmos isn't configured or nothing's ever been
# activated -- a RAG config outage never breaks resume parsing, it just
# means the admin console hasn't changed anything from this script's
# original behavior yet.
from rag_config import get_runtime_config, get_active_prompt_template, sanitize_for_prompt  # noqa: E402

RAG_RUNTIME_CONFIG = get_runtime_config()
CHUNK_SUMMARIZATION_PROMPT = get_active_prompt_template("resume_chunk_summarization")
MERGE_EXTRACTION_PROMPT = get_active_prompt_template("resume_merge_structured_extraction")

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

def summarize_pdf(pdf_path, jd, jd_vector, semantic_store, trace_id, correlation_id):
    # One AI Observability Center trace per resume (trace_id, generated by
    # the caller and threaded through every stage below) -- this is the
    # one real, single-process, end-to-end AI workflow in this repo (see
    # docs/AI_OBSERVABILITY_AZURE_HUB.md's investigation notes: ResumeParser.py
    # and ResumeMEtrics.py are separate scripts/processes, so this is the
    # highest-value workflow that can actually be traced start-to-finish
    # within one call stack). correlation_id is the resume's deterministic
    # candidate_id -- the external business key an engineer would actually
    # search by, distinct from the internal trace_id grouping key.
    with observability.trace_stage("resume_parsing", "document_extraction", trace_id=trace_id, correlation_id=correlation_id):
        resume_text = extract_text_from_pdf(pdf_path)
    email, phone, linkedin = extract_contact_details(resume_text)

    # Section-aware chunk + embed the whole resume, and persist every chunk
    # to the semantic index (search stays complete even though only the
    # top-K most JD-relevant chunks get summarized below). Chunking and
    # embedding are each their own real, separately-timed stage --
    # embed_and_index_resume_text() records both spans itself (see its
    # docstring) once trace_id/correlation_id are threaded through, so
    # this resume's trace shows them as two distinct, correctly-ordered
    # stages, not one opaque fused span.
    chunking_cfg = RAG_RUNTIME_CONFIG["chunking"]
    retrieval_cfg = RAG_RUNTIME_CONFIG["retrieval"]
    top_k = retrieval_cfg["top_k"]
    similarity_threshold = retrieval_cfg["similarity_threshold"]

    all_chunk_results = embed_and_index_resume_text(
        pdf_path.name, resume_text, semantic_store, trace_id=trace_id, correlation_id=correlation_id,
        max_chars=chunking_cfg["max_chars"],
    )

    with observability.trace_stage(
        "resume_parsing", "retrieval_ranking", trace_id=trace_id, correlation_id=correlation_id,
        metadata={"top_k": top_k, "similarity_threshold": similarity_threshold, "total_chunks": len(all_chunk_results)},
    ):
        selected = rank_by_relevance(all_chunk_results, jd_vector, top_k=top_k, similarity_threshold=similarity_threshold)

    write_log(
        f"Semantic ranking for {pdf_path.name}: using {len(selected)}/{len(all_chunk_results)} chunks "
        f"for summarization (all {len(all_chunk_results)} persisted to the semantic index)."
    )
    chunks = [r["text"] for r in selected]

    # Insufficient-evidence fallback (RAG Configuration Console's grounding
    # requirement): only reachable when an admin has configured a
    # similarity_threshold > 0 and NO chunk clears it -- with the default
    # threshold of 0.0, `chunks` is never empty here (unless the resume had
    # zero extractable chunks at all), so this never changes behavior for
    # an unconfigured deployment.
    if not chunks:
        write_log(f"No chunks met the similarity threshold ({similarity_threshold}) for {pdf_path.name} -- skipping summarization.")
        fallback_name = extract_name_from_filename(pdf_path.name)
        structured_data = {
            "Name": fallback_name,
            "Profile Summary": "No sufficiently relevant resume content was found for this job description "
                                f"(similarity threshold {similarity_threshold} not met by any chunk).",
            "JD Match": "0%",
        }
        structured_data["Email"] = email
        structured_data["Phone"] = phone
        structured_data["LinkedIn"] = linkedin
        return structured_data

    summaries = []

    # Resume text (chunks) and the JD are UNTRUSTED input -- a candidate
    # fully controls their resume's content -- so both are sanitized
    # before interpolation into any prompt (see rag_config.py's docstring
    # for exactly what this does and doesn't protect against).
    sanitized_jd = sanitize_for_prompt(jd)

    gen_cfg = RAG_RUNTIME_CONFIG["generation"]["chunk_summarization"]
    llm = get_chat_llm(temperature=gen_cfg["temperature"], max_tokens=gen_cfg["max_tokens"], model=gen_cfg["model"])
    for chunk in chunks:
        prompt = CHUNK_SUMMARIZATION_PROMPT["template"].format(text=sanitize_for_prompt(chunk), jd=sanitized_jd)
        partial_summary = call_with_retry(
            llm.predict, prompt,
            on_retry=lambda attempt, e: write_log(f"Retry {attempt} for chunk summarization on {pdf_path.name}: {e}"),
            label="resume_chunk_summarization", model=gen_cfg["model"],
            trace_id=trace_id, correlation_id=correlation_id,
            prompt_version=str(CHUNK_SUMMARIZATION_PROMPT["version"]), workflow="resume_parsing",
        )
        summaries.append(partial_summary)

    # Summaries are LLM-produced from untrusted chunk text, so they could
    # still echo injected content back out -- sanitized again before the
    # merge step's prompt, same as the chunk text and JD above.
    merge_prompt = MERGE_EXTRACTION_PROMPT["template"].format(
        summaries=sanitize_for_prompt("".join(summaries)), jd=sanitized_jd,
    )
    merge_cfg = RAG_RUNTIME_CONFIG["generation"]["merge_extraction"]
    llm_merge = get_chat_llm(
        temperature=merge_cfg["temperature"], max_tokens=merge_cfg["max_tokens"], model=merge_cfg["model"],
        model_kwargs={"response_format": {"type": "json_object"}},
    )
    final_response = call_with_retry(
        llm_merge.predict, merge_prompt,
        on_retry=lambda attempt, e: write_log(f"Retry {attempt} for merge step on {pdf_path.name}: {e}"),
        label="resume_merge_structured_extraction", model=merge_cfg["model"],
        trace_id=trace_id, correlation_id=correlation_id,
        prompt_version=str(MERGE_EXTRACTION_PROMPT["version"]), workflow="resume_parsing",
    )

    # The span below is marked "failed" on a genuine JSONDecodeError (the
    # exception is re-raised inside the `with` so trace_stage records it
    # accurately), but the pipeline itself still recovers via the
    # filename-based fallback immediately after -- a traced failure here
    # means "the model's output didn't validate," not "this resume's
    # processing aborted."
    try:
        with observability.trace_stage(
            "resume_parsing", "output_parsing_validation", trace_id=trace_id, correlation_id=correlation_id,
        ):
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

    # One AI Observability Center trace per resume -- trace_id is purely
    # an internal grouping key; correlation_id (the deterministic
    # candidate_id) is the external business key an engineer would
    # actually search the trace explorer by. See summarize_pdf()'s
    # docstring comment for the full stage list this trace covers.
    resume_trace_id = observability.new_trace_id()
    resume_correlation_id = candidate_id_for(filename)

    try:
        parsed_data = summarize_pdf(pdf_path, jd, jd_vector, semantic_store, resume_trace_id, resume_correlation_id)
        parsed_data["resume_name"] = filename
        all_data.append(parsed_data)
        semantic_store.save()  # persisted per-resume so a later crash doesn't lose earlier progress

        try:
            with observability.trace_stage(
                "resume_parsing", "persistence", trace_id=resume_trace_id, correlation_id=resume_correlation_id,
            ):
                lake_doc = dict(parsed_data)
                lake_doc["candidate_id"] = resume_correlation_id
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




