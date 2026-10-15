"""Pure scoring functions used by ResumeMEtrics.py, split out into their own
module so they're unit-testable without pulling in ResumeMEtrics.py's
module-level side effects (reading resume_summary.csv, embedding the JD,
writing candidate_ranking_metrics.csv — all of which run immediately at
import time in the original script).

Functions that previously read `config` as an implicit module-level global
now take it as an explicit parameter — same for `jd_vector` /
`semantic_store` in the semantic-match functions, which were likewise
closures over module globals before this refactor.
"""

import ast
import sys
from pathlib import Path

_semantic_lib_dir = Path(__file__).resolve().parent / "semantic-library"
sys.path.insert(0, str(_semantic_lib_dir))
from vector_store import cosine_scores  # noqa: E402


def certificate_score(cert_str):
    try:
        certs = ast.literal_eval(cert_str)
        return len(certs) if isinstance(certs, list) else 0
    except Exception:
        return 0


def project_score(projects_str):
    try:
        projects = ast.literal_eval(projects_str)
        return 5 * sum(1 for p in projects if isinstance(p, dict) and p.get("Relevance to JD", "").strip().lower() == "high")
    except Exception:
        return 0


def skill_score(row, config):
    match = config["skill_rules"]["match"]
    missing = config["skill_rules"]["missing"]
    try:
        matched = ast.literal_eval(str(row["Top Matching Keywords"]))
        matched_count = len(matched)
    except Exception:
        matched_count = 0
    try:
        missing_dict = ast.literal_eval(str(row["Missing Keywords"]))
        missing_count = sum(len(v) for v in missing_dict.values())
    except Exception:
        missing_count = 0
    return round((match * matched_count) + (missing * missing_count), 2)


def contribute_score(contrib_str):
    try:
        skills = ast.literal_eval(contrib_str)
        return 3 * len(skills) if isinstance(skills, list) else 0
    except Exception:
        return 0


def soft_skill_score(soft_str):
    try:
        skills = ast.literal_eval(soft_str)
        return 2 * len(skills) if isinstance(skills, list) else 0
    except Exception:
        return 0


def industry_penalty(industry_str):
    if isinstance(industry_str, str):
        industry = industry_str.strip().lower()
        if any(term in industry for term in ["tech", "technology", "it", "cs", "computer"]):
            return 0
    return -0.25


def culture_score(fit_str, config):
    if isinstance(fit_str, str):
        level = fit_str.strip().split("-")[0].strip().lower()
        return config["culture_fit_score"].get(level, 0)
    return 0


def effort_score(effort_str, config):
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
    except Exception:
        red_penalty = 0
    try:
        concerns = ast.literal_eval(row["Potential Concerns"])
        concern_penalty = -0.1 * len(concerns) if isinstance(concerns, list) else 0
    except Exception:
        concern_penalty = 0
    return round(gap_penalty + red_penalty + concern_penalty, 2)


def jd_match_score(jd_str):
    try:
        return float(jd_str.strip('%')) / 10
    except Exception:
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


def semantic_match_score_or_fallback(row, jd_vector, semantic_store):
    score = semantic_match_score(row["resume_name"], jd_vector, semantic_store)
    return score if score is not None else jd_match_score(row["JD Match"])


def experience_score(exp_str, config):
    try:
        exp = float(exp_str)
        thresholds = config["experience_score"]["thresholds"]
        scores = config["experience_score"]["scores"]
        if exp > thresholds[1]:
            return scores[2]
        elif exp > thresholds[0]:
            return scores[1]
        else:
            return scores[0]
    except Exception:
        return 0


def candidate_type_score(type_str, config):
    if isinstance(type_str, str):
        level = type_str.strip().lower()
        return config["candidate_type_score"].get(level, 0)
    return 0


def classify_candidate(idx, top_cutoff, mid_cutoff):
    if idx < top_cutoff:
        return "Recommended for Fast-Track"
    elif idx < mid_cutoff:
        return "To be Considered"
    else:
        return "Rejected"
