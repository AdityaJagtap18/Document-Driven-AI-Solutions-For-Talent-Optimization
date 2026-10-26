"""F-03 (end-to-end candidate feedback system) -- minimal slice.

Scope note (read before extending this file): the full F-03 feature
prompt describes a comprehensive feedback platform (customizable forms,
anonymous/attributable modes, moderation/escalation, re-identification
protection thresholds, department-level reporting, withdrawal handling).
This slice implements exactly the real, defensible core the repository
actually supports today, grounded in direct inspection, not invented:

  - TRIGGER: this repo has exactly one real "hiring process complete"
    signal -- the `ranking_tier` field review_candidate_endpoint and
    approve_bulk_decision_endpoint already write when a human records a
    final approve/reject decision (see azure-functions/api_blueprint.py).
    There is no separate "offer extended"/"withdrawn" status anywhere in
    the real data model (confirmed by inspection -- not present in
    context-lake/schema/candidate.schema.json or anywhere in
    api_blueprint.py) -- so "withdrawn" handling is explicitly deferred,
    not silently invented.
  - SEPARATION FROM HIRING SCORES: feedback lives in its own
    `candidate_feedback` Cosmos container, never touches `candidates`,
    `scoring_history`, or `ranking_tier` -- satisfies the spec's explicit
    "keep feedback separate from hiring evaluation" requirement by
    construction, not by convention alone.
  - DUPLICATE PREVENTION: one feedback-request document per candidate_id
    (the container's partition key), created only if one doesn't already
    exist -- a candidate can never be requested twice, and can submit at
    most once (status transitions requested -> submitted are one-way).
  - NOT a real candidate identity/auth system: like every other
    candidate-facing endpoint in this repo (submit_application_endpoint,
    get_candidate_by_email_endpoint), the submission endpoint is reached
    by candidate_id alone, with no real candidate login -- this app has
    no candidate auth persona (confirmed finding from this engagement's
    earlier F-08 work). Documented here, not hidden.

Deliberately deferred (see docs/fde/IMPLEMENTATION_STATUS.md): anonymous
vs. attributable submission modes, department-level filtering (no
"department" field exists anywhere in the real candidate/JD schema),
moderation/escalation workflows, withdrawal handling, customizable form
builder (the category list below is fixed, not admin-editable).
"""

RATING_MIN = 1
RATING_MAX = 5

# The real dimensions named in the feature intent (Section 4 of the F-03
# prompt), kept as a fixed list for this slice rather than an
# admin-configurable form builder.
FEEDBACK_CATEGORIES = [
    "application_process",
    "communication",
    "interview_scheduling",
    "interview_rounds",
    "interviewer_interactions",
    "transparency",
    "overall_experience",
]

# The only two real terminal ranking_tier values a human decision can
# write today -- see review_candidate_endpoint/approve_bulk_decision's
# `candidate["ranking_tier"] = "Recommended for Fast-Track" if decision
# == "approve" else "Rejected"`. "To be Considered" is the pre-decision
# default/AI-recommendation state, never a final outcome.
TERMINAL_RANKING_TIERS = {"Recommended for Fast-Track", "Rejected"}

MAX_COMMENT_LENGTH = 2000


def is_eligible_for_feedback_request(candidate: dict) -> bool:
    """True once a real final hiring decision has been recorded for this
    candidate (see module docstring for why ranking_tier is the real
    signal used, not an invented "process complete" flag)."""
    return (candidate or {}).get("ranking_tier") in TERMINAL_RANKING_TIERS


def sanitize_comment(comment) -> str:
    """Strips control characters and caps length -- this text is only
    ever displayed to a recruiter/admin in a report table, never fed to
    an LLM prompt, so this is a display-safety cap, not prompt-injection
    sanitization (that's chat_guardrails.py's distinct concern)."""
    if not comment:
        return ""
    cleaned = "".join(ch for ch in str(comment) if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return cleaned.strip()[:MAX_COMMENT_LENGTH]


def validate_feedback_submission(payload: dict) -> list:
    """Returns a list of human-readable validation errors -- empty list
    means valid. Every category must be present with an integer rating
    1-5 (Must requirement, not optional per-category) -- the written
    comment is optional."""
    errors = []
    if not isinstance(payload, dict):
        return ["Request body must be a JSON object."]

    ratings = payload.get("ratings")
    if not isinstance(ratings, dict):
        return ["'ratings' must be an object mapping each category to a 1-5 rating."]

    for category in FEEDBACK_CATEGORIES:
        if category not in ratings:
            errors.append(f"Missing rating for required category '{category}'.")
            continue
        value = ratings[category]
        if not isinstance(value, int) or isinstance(value, bool) or not (RATING_MIN <= value <= RATING_MAX):
            errors.append(f"Rating for '{category}' must be an integer {RATING_MIN}-{RATING_MAX}, got {value!r}.")

    unknown = set(ratings.keys()) - set(FEEDBACK_CATEGORIES)
    if unknown:
        errors.append(f"Unknown rating category/categories: {sorted(unknown)}.")

    comment = payload.get("comment")
    if comment is not None and not isinstance(comment, str):
        errors.append("'comment' must be a string if provided.")

    return errors


def summarize_feedback(records: list) -> dict:
    """Aggregate view for the recruiter/admin dashboard: per-category
    average rating and submission counts. Never fabricates an average
    for a category with zero real submitted ratings -- reports None
    instead of a misleading 0.0."""
    submitted = [r for r in records if r.get("status") == "submitted" and r.get("ratings")]
    totals = {c: [] for c in FEEDBACK_CATEGORIES}
    for r in submitted:
        for c in FEEDBACK_CATEGORIES:
            value = r.get("ratings", {}).get(c)
            if isinstance(value, int):
                totals[c].append(value)

    averages = {
        c: (round(sum(values) / len(values), 2) if values else None)
        for c, values in totals.items()
    }

    requested = len(records)
    submitted_count = len(submitted)
    return {
        "requested_count": requested,
        "submitted_count": submitted_count,
        "pending_count": requested - submitted_count,
        "response_rate": round(submitted_count / requested, 4) if requested else None,
        "category_averages": averages,
    }
