"""F-21 (AI evaluation/QA framework) -- minimal slice.

Scope note (read before extending this file): the full F-21 feature
prompt describes a portfolio-scale framework covering every AI-powered
feature in the app (chatbots, scoring, matching, routing, fairness,
production monitoring, dashboards, release gates). This module is
deliberately NOT that. It is the smallest real, defensible slice,
explicitly scoped by the prior roadmap assessment: ONE quality gate on
the real resume-scoring pipeline's structured-output contract, plus one
LLM-as-judge candidate-job match-quality check, reusing F-19's judge
infrastructure directly (rag_evaluation.parse_judge_response /
GroundednessJudgeError) rather than duplicating it -- this is the
concrete "reuse the separate RAG evaluation framework where appropriate"
requirement from the F-21 prompt itself.

Everything else in the full prompt (dashboards, scheduled runs,
multi-model/multi-config regression gates, fairness evaluation, judge
calibration against human review, production sampling/monitoring) is
explicitly deferred, not silently dropped -- see
docs/fde/IMPLEMENTATION_STATUS.md.

STRUCTURED-OUTPUT CONTRACT THIS GATE CHECKS: the real shape
rag_config.DEFAULT_MERGE_EXTRACTION_TEMPLATE asks the LLM to return, and
that resume_scoring.py's real scoring functions assume -- NOT invented
here. A violation of this contract matters for a real reason: several
resume_scoring.py functions (skill_score, penalty_score,
semantic_match_score_or_fallback) wrap their field reads in bare
try/except and silently return 0 on a parse failure -- so a malformed
LLM extraction doesn't crash the pipeline, it SILENTLY UNDER-SCORES a
real candidate with no visible error. This gate makes that failure mode
visible instead of silent.
"""

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "context-lake"))

import repositories  # noqa: E402
import rag_evaluation  # noqa: E402 -- reused directly, not duplicated (see module docstring)
from rag_config import sanitize_for_prompt  # noqa: E402

_VALID_CANDIDATE_TYPES = {"junior", "mid-level", "senior"}
_VALID_RELEVANCE_LEVELS = {"high", "medium", "low"}
_JD_MATCH_PATTERN = re.compile(r"^\s*\d{1,3}\s*%?\s*$")


def validate_structured_extraction(record: dict) -> list:
    """Deterministic, rule-based structured-output validation against the
    REAL contract rag_config.DEFAULT_MERGE_EXTRACTION_TEMPLATE defines.
    Returns a list of human-readable violation strings -- empty list
    means clean. Never raises on a malformed record; a missing/wrong-typed
    field is itself what this function is meant to catch and report, not
    crash on."""
    errors = []

    score = record.get("Resume Strength Score")
    if not isinstance(score, (int, float)) or not (0 <= score <= 10):
        errors.append(f"Resume Strength Score must be a number 0-10, got {score!r}")

    candidate_type = record.get("Candidate Type")
    if not isinstance(candidate_type, str) or candidate_type.strip().lower() not in _VALID_CANDIDATE_TYPES:
        errors.append(f"Candidate Type must be one of Junior/Mid-Level/Senior, got {candidate_type!r}")

    gaps = record.get("Employment Gaps Detected")
    if not isinstance(gaps, bool):
        errors.append(f"Employment Gaps Detected must be a boolean, got {gaps!r}")

    jd_match = record.get("JD Match")
    if not isinstance(jd_match, str) or not _JD_MATCH_PATTERN.match(jd_match):
        errors.append(f"JD Match must be a percentage-shaped string (e.g. '90%'), got {jd_match!r}")

    missing_keywords = record.get("Missing Keywords")
    if not isinstance(missing_keywords, dict):
        errors.append(f"Missing Keywords must be an object, got {type(missing_keywords).__name__}")
    else:
        for key in ("Technical Skills", "Tools & Technologies", "Concepts & Methodologies"):
            if key in missing_keywords and not isinstance(missing_keywords[key], list):
                errors.append(f"Missing Keywords.{key} must be a list, got {type(missing_keywords[key]).__name__}")

    top_matching = record.get("Top Matching Keywords")
    if top_matching is not None and not isinstance(top_matching, list):
        errors.append(f"Top Matching Keywords must be a list, got {type(top_matching).__name__}")

    projects = record.get("Projects")
    if projects is not None:
        if not isinstance(projects, list):
            errors.append(f"Projects must be a list, got {type(projects).__name__}")
        else:
            for i, p in enumerate(projects):
                if not isinstance(p, dict):
                    errors.append(f"Projects[{i}] must be an object, got {type(p).__name__}")
                    continue
                relevance = p.get("Relevance to JD")
                if relevance is not None and (not isinstance(relevance, str) or relevance.strip().lower() not in _VALID_RELEVANCE_LEVELS):
                    errors.append(f"Projects[{i}].Relevance to JD must be High/Medium/Low, got {relevance!r}")

    return errors


def run_structured_output_gate(candidate_ids: list = None) -> dict:
    """Runs the structured-output gate against REAL candidate records
    already in Cosmos -- never synthetic/invented records. If
    `candidate_ids` is None, evaluates every real candidate currently in
    the candidates container (repositories.list_candidates()). A
    candidate whose record can't be read is reported as its own error
    entry, never silently skipped."""
    if candidate_ids is None:
        try:
            candidates = repositories.list_candidates()
        except Exception as e:
            return {"total": 0, "passed": 0, "pass_rate": None, "per_candidate": [], "error": f"Could not list candidates: {str(e)[:300]}"}
    else:
        candidates = []
        for cid in candidate_ids:
            try:
                c = repositories.get_candidate(cid)
            except Exception as e:
                candidates.append({"candidate_id": cid, "_fetch_error": str(e)[:300]})
                continue
            if c is None:
                candidates.append({"candidate_id": cid, "_fetch_error": "not found"})
            else:
                candidates.append(c)

    results = []
    for c in candidates:
        cid = c.get("candidate_id", "unknown")
        if "_fetch_error" in c:
            results.append({"candidate_id": cid, "passed": False, "error": c["_fetch_error"]})
            continue
        violations = validate_structured_extraction(c)
        results.append({"candidate_id": cid, "passed": not violations, "violations": violations})

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "total": total, "passed": passed,
        "pass_rate": round(passed / total, 4) if total else None,
        "per_candidate": results,
    }


# ---------------------------------------------------------------------------
# LLM-as-judge candidate-job match-quality check -- reuses F-19's judge
# response validation directly (rag_evaluation.parse_judge_response /
# GroundednessJudgeError). The {"groundedness_score", "rationale"} shape
# is generic enough (an integer 1-5 quality score + a rationale) to serve
# this judge too without semantic drift: here it scores "does the
# candidate's JD Match percentage look consistent with their extracted
# skills/keywords," not groundedness, but the validation logic (score
# range, non-empty rationale, malformed-JSON handling) is identical and
# genuinely shared, not just copy-pasted.
# ---------------------------------------------------------------------------
_MATCH_QUALITY_JUDGE_PROMPT = """
You are a strict evaluator checking whether a candidate's stated JD-match
percentage is CONSISTENT with their extracted skills and keywords -- i.e.
whether the match score looks supportable given what the candidate's
profile actually shows, not inflated or understated.

Candidate's extracted profile:
\"\"\"{profile}\"\"\"

Job description:
\"\"\"{jd_text}\"\"\"

Stated JD Match: {jd_match}

Return ONLY a JSON object with this exact shape:
{{
  "groundedness_score": <integer 1-5, where 5 = the stated match
    percentage is well supported by the extracted skills/keywords; 1 =
    the match percentage looks unsupported or contradicted by the
    profile>,
  "rationale": "<one or two sentences citing specifically what does or
    doesn't support the stated match>"
}}
"""


def build_match_quality_judge_prompt(profile: dict, jd_text: str, jd_match: str) -> str:
    """`profile` (the candidate's real extracted fields) and `jd_text`
    are both untrusted, resume/JD-derived content -- sanitized before
    interpolation, same F-10 discipline as every other prompt-building
    function in this repo."""
    sanitized_profile = sanitize_for_prompt(str(profile))
    sanitized_jd = sanitize_for_prompt(jd_text)
    sanitized_match = sanitize_for_prompt(str(jd_match))
    return _MATCH_QUALITY_JUDGE_PROMPT.format(profile=sanitized_profile, jd_text=sanitized_jd, jd_match=sanitized_match)
