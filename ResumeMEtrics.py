#!/usr/bin/env python
# coding: utf-8

import pandas as pd
import json
import sys
from pathlib import Path

from resume_scoring import (
    certificate_score, project_score, skill_score, contribute_score,
    soft_skill_score, industry_penalty, culture_score, effort_score,
    penalty_score, jd_match_score, semantic_match_score_or_fallback,
    experience_score, candidate_type_score, classify_candidate,
)

# === Semantic search wiring (semantic-library/) ===
# Adds a 13th scoring dimension, "Semantic Match Score": best-chunk cosine
# similarity (0-10 scale) between a candidate's indexed resume chunks and the
# JD, computed from the same embeddings ResumeParser.py persists. This is
# additive, not a replacement — the existing LLM self-reported "JD Match
# Score" (jd_match_score(), in resume_scoring.py) is kept as-is so the two
# can be compared. If a candidate's resume isn't in the semantic index yet
# (not yet processed by the updated ResumeParser.py, or re-embedding
# failed), falls back to that candidate's JD Match Score for this new column too.
_semantic_lib_dir = Path(__file__).resolve().parent / "semantic-library"
sys.path.insert(0, str(_semantic_lib_dir))
from vector_store import LocalVectorStore  # noqa: E402

SEMANTIC_INDEX_PATH = _semantic_lib_dir / "index" / "resume-chunks.json"
SEMANTIC_JD_FOLDER = Path("jd_folder")


def load_jd_vector(jd_folder: Path):
    """Embeds the most recently modified JD in jd_folder. Returns None (with
    a printed warning) if no JD file exists or embedding fails for any
    reason — callers must treat None as 'fall back to JD Match Score'."""
    jd_files = list(jd_folder.glob("*.txt")) if jd_folder.exists() else []
    if not jd_files:
        print("[WARN] No JD file found in jd_folder/; Semantic Match Score will fall back to JD Match Score for every candidate.")
        return None
    latest_jd_file = max(jd_files, key=lambda f: f.stat().st_mtime)
    try:
        from embeddings import embed_text  # imported here: only needed if a JD file exists

        return embed_text(latest_jd_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] Could not embed JD for semantic scoring ({e}); "
              f"Semantic Match Score will fall back to JD Match Score for every candidate.")
        return None


def compute_candidate_metrics(df: pd.DataFrame, config: dict, jd_vector, semantic_store) -> pd.DataFrame:
    """Pure-ish transform: takes a resume_summary-shaped DataFrame + config +
    (optional) semantic search context, returns it with all raw score
    columns, normalized columns, weighted score, and rank added. Does not
    read or write any files — kept separate from main() so this is testable
    with a small synthetic DataFrame."""
    weights = config["weights"]
    norm_multiplier = config["normalize_multiplier"]

    df = df.copy()
    df.insert(df.columns.get_loc("JD Match") + 1, "JD Match Score", df["JD Match"].apply(jd_match_score))
    df.insert(
        df.columns.get_loc("JD Match Score") + 1,
        "Semantic Match Score",
        df.apply(lambda row: semantic_match_score_or_fallback(row, jd_vector, semantic_store), axis=1),
    )
    df.insert(df.columns.get_loc("Relevant Experience (yrs)") + 1, "Experience Score",
              df["Relevant Experience (yrs)"].apply(lambda x: experience_score(x, config)))
    df.insert(df.columns.get_loc("Candidate Type") + 1, "Candidate Type Score",
              df["Candidate Type"].apply(lambda x: candidate_type_score(x, config)))
    df.insert(df.columns.get_loc("Projects") + 1, "Project Score", df["Projects"].apply(project_score))
    df.insert(df.columns.get_loc("Certifications & Courses") + 1, "Certificate Score",
              df["Certifications & Courses"].apply(certificate_score))
    df.insert(df.columns.get_loc("Top Matching Keywords") + 1, "Skill Score",
              df.apply(lambda row: skill_score(row, config), axis=1))
    df.insert(df.columns.get_loc("Skills That Will Contribute to the Company") + 1, "Contribute Score",
              df["Skills That Will Contribute to the Company"].apply(contribute_score))
    df.insert(df.columns.get_loc("Soft Skills & Leadership Qualities") + 1, "Soft Skill Score",
              df["Soft Skills & Leadership Qualities"].apply(soft_skill_score))
    df.insert(df.columns.get_loc("Industry Experience") + 1, "Industry Penalty",
              df["Industry Experience"].apply(industry_penalty))
    df.insert(df.columns.get_loc("Culture Fit Assessment") + 1, "Cultural Fit Score",
              df["Culture Fit Assessment"].apply(lambda x: culture_score(x, config)))
    df.insert(df.columns.get_loc("Effort Needed by the Company") + 1, "Effort Score",
              df["Effort Needed by the Company"].apply(lambda x: effort_score(x, config)))
    df.insert(df.columns.get_loc("Employment Gaps Detected") + 1, "Penalty Score",
              df.apply(penalty_score, axis=1))

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

    df["Normalized Weighted Score"] = sum(
        df[f"Norm {col}"] * wt for col, wt in weights.items()
    ).round(2)
    df["Normalized Rank"] = df["Normalized Weighted Score"].rank(method="dense", ascending=False).astype(int)

    return df


def add_selection_recommendation(df: pd.DataFrame) -> pd.DataFrame:
    """Sorts by Normalized Rank and adds the tiering column. Separate
    function (matches the original script's two-pass structure) so it's
    testable on its own with a small synthetic DataFrame."""
    df = df.sort_values(by="Normalized Rank", ascending=True).reset_index(drop=True)
    n = len(df)
    top_cutoff = int(n * 0.25)
    mid_cutoff = int(n * 0.75)
    df["Selection Recommendation"] = df.index.map(lambda idx: classify_candidate(idx, top_cutoff, mid_cutoff))
    return df


def main():
    metrics_output_folder = Path("CandidateMetrics_Folder")
    metrics_output_folder.mkdir(parents=True, exist_ok=True)
    metrics_csv_path = metrics_output_folder / "candidate_ranking_metrics.csv"

    df = pd.read_csv("Resume_Parsed_CSVs/resume_summary.csv")

    with open("config/scoring_config.json", "r") as f:
        config = json.load(f)

    semantic_store = LocalVectorStore(str(SEMANTIC_INDEX_PATH))
    jd_vector = load_jd_vector(SEMANTIC_JD_FOLDER)

    df = compute_candidate_metrics(df, config, jd_vector, semantic_store)
    df.to_csv(metrics_csv_path, index=False)
    print(f"✅ Saved to: {metrics_csv_path.resolve()} using config scoring.")

    df = pd.read_csv(metrics_csv_path)
    df = add_selection_recommendation(df)
    df.to_csv(metrics_csv_path, index=False)

    print("Updated original CSV with sorted 'Normalized Rank' and added 'Selection Recommendation'.")
    print(df.head(10).to_string())


if __name__ == "__main__":
    main()
