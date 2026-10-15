#!/usr/bin/env python
# coding: utf-8

# In[3]:


import pandas as pd
import ast
import json
import sys
from pathlib import Path

# === Semantic search wiring (semantic-library/) ===
# Adds a 13th scoring dimension, "Semantic Match Score": best-chunk cosine
# similarity (0-10 scale) between a candidate's indexed resume chunks and the
# JD, computed from the same embeddings ResumeParser.py persists. This is
# additive, not a replacement — the existing LLM self-reported "JD Match
# Score" (jd_match_score(), below) is kept as-is so the two can be compared.
# If a candidate's resume isn't in the semantic index yet (not yet processed
# by the updated ResumeParser.py, or re-embedding failed), falls back to that
# candidate's JD Match Score for this new column too.
_semantic_lib_dir = Path(__file__).resolve().parent / "semantic-library"
sys.path.insert(0, str(_semantic_lib_dir))
from vector_store import LocalVectorStore, cosine_scores  # noqa: E402

SEMANTIC_INDEX_PATH = _semantic_lib_dir / "index" / "resume-chunks.json"
SEMANTIC_JD_FOLDER = Path("jd_folder")

# === Create output folder ===
metrics_output_folder = Path("CandidateMetrics_Folder")
metrics_output_folder.mkdir(parents=True, exist_ok=True)

# Define full path to output CSV
metrics_csv_path = metrics_output_folder / "candidate_ranking_metrics.csv"

# === Load resume summary ===
df = pd.read_csv("Resume_Parsed_CSVs/resume_summary.csv")

# === Load scoring config ===
with open("config/scoring_config.json", "r") as f:
    config = json.load(f)

weights = config["weights"]
norm_multiplier = config["normalize_multiplier"]

# === Load JD + embed once, and open the semantic index (graceful if either is unavailable) ===
semantic_store = LocalVectorStore(str(SEMANTIC_INDEX_PATH))
jd_vector = None
jd_files = list(SEMANTIC_JD_FOLDER.glob("*.txt")) if SEMANTIC_JD_FOLDER.exists() else []
if jd_files:
    latest_jd_file = max(jd_files, key=lambda f: f.stat().st_mtime)
    try:
        from embeddings import embed_text  # imported here: only needed if a JD file exists

        jd_vector = embed_text(latest_jd_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] Could not embed JD for semantic scoring ({e}); "
              f"Semantic Match Score will fall back to JD Match Score for every candidate.")
else:
    print("[WARN] No JD file found in jd_folder/; Semantic Match Score will fall back to JD Match Score for every candidate.")

# === Scoring Functions ===

def certificate_score(cert_str):
    try:
        certs = ast.literal_eval(cert_str)
        return len(certs) if isinstance(certs, list) else 0
    except:
        return 0

def project_score(projects_str):
    try:
        projects = ast.literal_eval(projects_str)
        return 5 * sum(1 for p in projects if isinstance(p, dict) and p.get("Relevance to JD", "").strip().lower() == "high")
    except:
        return 0

def skill_score(row):
    match = config["skill_rules"]["match"]
    missing = config["skill_rules"]["missing"]
    try:
        matched = ast.literal_eval(str(row["Top Matching Keywords"]))
        matched_count = len(matched)
    except:
        matched_count = 0
    try:
        missing_dict = ast.literal_eval(str(row["Missing Keywords"]))
        missing_count = sum(len(v) for v in missing_dict.values())
    except:
        missing_count = 0
    return round((match * matched_count) + (missing * missing_count), 2)

def contribute_score(contrib_str):
    try:
        skills = ast.literal_eval(contrib_str)
        return 3 * len(skills) if isinstance(skills, list) else 0
    except:
        return 0

def soft_skill_score(soft_str):
    try:
        skills = ast.literal_eval(soft_str)
        return 2 * len(skills) if isinstance(skills, list) else 0
    except:
        return 0

def industry_penalty(industry_str):
    if isinstance(industry_str, str):
        industry = industry_str.strip().lower()
        if any(term in industry for term in ["tech", "technology", "it", "cs", "computer"]):
            return 0
    return -0.25

def culture_score(fit_str):
    if isinstance(fit_str, str):
        level = fit_str.strip().split("-")[0].strip().lower()
        return config["culture_fit_score"].get(level, 0)
    return 0


def effort_score(effort_str):
    if isinstance(effort_str, str):
        level = effort_str.strip().split("-")[0].strip().lower()
        return config["effort_needed_score"].get(level, 0)
    return 0


def penalty_score(row):
    gap = str(row["Employment Gaps Detected"]).strip().lower() == "true"
    gap_penalty = -5 if gap else 0
    try:
        red_flags = ast.literal_eval(row["Red Flags & Risk Analysis"])
        red_penalty = -0.5 * len(red_flags) if isinstance(red_flags, list) else 0
    except:
        red_penalty = 0
    try:
        concerns = ast.literal_eval(row["Potential Concerns"])
        concern_penalty = -0.1 * len(concerns) if isinstance(concerns, list) else 0
    except:
        concern_penalty = 0
    return round(gap_penalty + red_penalty + concern_penalty, 2)

def jd_match_score(jd_str):
    try:
        return float(jd_str.strip('%')) / 10
    except:
        return 0

def semantic_match_score(resume_name, jd_vector, semantic_store):
    """Best-chunk cosine similarity (0-10 scale, same range as
    jd_match_score) between this resume's indexed chunks and the JD. Returns
    None if the resume isn't in the semantic index yet or no JD embedding is
    available — caller falls back to jd_match_score() in that case."""
    if jd_vector is None:
        return None
    chunks = semantic_store.find_by_metadata("resume_name", resume_name)
    if not chunks:
        return None
    vectors = [c["vector"] for c in chunks]
    best = float(max(cosine_scores(vectors, jd_vector)))
    return round(max(0.0, best) * 10, 2)

def semantic_match_score_or_fallback(row):
    score = semantic_match_score(row["resume_name"], jd_vector, semantic_store)
    return score if score is not None else jd_match_score(row["JD Match"])

def experience_score(exp_str):
    try:
        exp = float(exp_str)
        thresholds = config["experience_score"]["thresholds"]
        scores = config["experience_score"]["scores"]
        if exp > thresholds[1]: return scores[2]
        elif exp > thresholds[0]: return scores[1]
        else: return scores[0]
    except:
        return 0

def candidate_type_score(type_str):
    if isinstance(type_str, str):
        level = type_str.strip().lower()
        return config["candidate_type_score"].get(level, 0)
    return 0

# === Insert Raw Score Columns ===

df.insert(df.columns.get_loc("JD Match") + 1, "JD Match Score", df["JD Match"].apply(jd_match_score))
df.insert(
    df.columns.get_loc("JD Match Score") + 1,
    "Semantic Match Score",
    df.apply(semantic_match_score_or_fallback, axis=1),
)
df.insert(df.columns.get_loc("Relevant Experience (yrs)") + 1, "Experience Score", df["Relevant Experience (yrs)"].apply(experience_score))
df.insert(df.columns.get_loc("Candidate Type") + 1, "Candidate Type Score", df["Candidate Type"].apply(candidate_type_score))
df.insert(df.columns.get_loc("Projects") + 1, "Project Score", df["Projects"].apply(project_score))
df.insert(df.columns.get_loc("Certifications & Courses") + 1, "Certificate Score", df["Certifications & Courses"].apply(certificate_score))
df.insert(df.columns.get_loc("Top Matching Keywords") + 1, "Skill Score", df.apply(skill_score, axis=1))
df.insert(df.columns.get_loc("Skills That Will Contribute to the Company") + 1, "Contribute Score", df["Skills That Will Contribute to the Company"].apply(contribute_score))
df.insert(df.columns.get_loc("Soft Skills & Leadership Qualities") + 1, "Soft Skill Score", df["Soft Skills & Leadership Qualities"].apply(soft_skill_score))
df.insert(df.columns.get_loc("Industry Experience") + 1, "Industry Penalty", df["Industry Experience"].apply(industry_penalty))
df.insert(df.columns.get_loc("Culture Fit Assessment") + 1, "Cultural Fit Score", df["Culture Fit Assessment"].apply(culture_score))
df.insert(df.columns.get_loc("Effort Needed by the Company") + 1, "Effort Score", df["Effort Needed by the Company"].apply(effort_score))
df.insert(df.columns.get_loc("Employment Gaps Detected") + 1, "Penalty Score", df.apply(penalty_score, axis=1))

# === Normalize Scores & Multiply ===

score_columns = list(weights.keys())
for col in score_columns:
    min_val = df[col].min()
    max_val = df[col].max()
    norm_col = f"Norm {col}"
    if max_val != min_val:
        norm_vals = (df[col] - min_val) / (max_val - min_val)
    else:
        norm_vals = pd.Series([0.0] * len(df), index=df.index)
    df.insert(df.columns.get_loc(col) + 1, norm_col, (norm_vals * norm_multiplier).round(2))

# === Weighted Normalized Score and Rank ===

df["Normalized Weighted Score"] = sum(
    df[f"Norm {col}"] * wt for col, wt in weights.items()
).round(2)

df["Normalized Rank"] = df["Normalized Weighted Score"].rank(method="dense", ascending=False).astype(int)

# === Save to CandidateMetrics_Folder ===

df.to_csv(metrics_csv_path, index=False)
print(f"✅ Saved to: {metrics_csv_path.resolve()} using config scoring.")


# In[5]:


import pandas as pd

# Load the original CSV
csv_path = "CandidateMetrics_Folder/candidate_ranking_metrics.csv"
df = pd.read_csv(csv_path)

# Sort the DataFrame by 'Normalized Rank' (ascending)
df.sort_values(by="Normalized Rank", ascending=True, inplace=True)
df.reset_index(drop=True, inplace=True)

# Calculate percentile cutoffs
n = len(df)
top_cutoff = int(n * 0.25)
mid_cutoff = int(n * 0.75)

# Add Selection Recommendation column
def classify_candidate(idx):
    if idx < top_cutoff:
        return "Recommended for Fast-Track"
    elif idx < mid_cutoff:
        return "To be Considered"
    else:
        return "Rejected"

df["Selection Recommendation"] = df.index.map(classify_candidate)

# Save back to the same CSV (overwrite)
df.to_csv(csv_path, index=False)

# Show preview
print("Updated original CSV with sorted 'Normalized Rank' and added 'Selection Recommendation'.")
print(df.head(10).to_string())


# In[ ]:




