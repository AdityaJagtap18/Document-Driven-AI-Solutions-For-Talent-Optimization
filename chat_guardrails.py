"""F-08 (Phase 1 Implementation Contract, Task 4): backend-enforced
authorization and prompt construction for the candidate-record chat Q&A
endpoint.

Scope note (see docs/fde-process and the Phase 1 contract's own correction):
this repo has no authenticated "candidate" persona -- the `candidate` role
carries zero permissions and no login path ever ties a JWT to a specific
candidate_id (the only candidate-facing surface, GET /candidates/by-email/
{email}, is ANONYMOUS and already documents its own real gap). Inventing
candidate authentication to scope this chat to a candidate session would be
rebuilding authentication, which is explicitly out of scope. This module
instead reuses the EXISTING staff-facing authorization model unchanged:
whoever already holds `candidates.read` today (admin, recruiter,
interviewer, hiring_manager, recruitment_ops_manager) may ask this chat
about any candidate record -- the same scope `get_candidate_endpoint`
already grants those roles, not a stricter or looser one invented here.

Pure functions only (no azure.functions.HttpRequest, no Flask) -- mirrors
jd_management.py's shape so the authorization-resolution logic is testable
without constructing a fake HTTP request, and the HTTP-layer wiring
(require_permission(), 401/403/404 mapping) stays entirely in
azure-functions/api_blueprint.py, exactly as every other endpoint does it.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "context-lake"))

import repositories  # noqa: E402
from rag_config import sanitize_for_prompt  # noqa: E402


class ChatNotFoundError(Exception):
    """The requested candidate_id does not resolve to a real record.
    Callers map this to 404 -- never 403, since reaching this point
    already means the caller's role is authorized; the record simply
    doesn't exist (same not-found-vs-forbidden distinction
    get_candidate_endpoint already makes)."""


# Fields surfaced to the chat context -- the same full-record scope
# get_candidate_endpoint already grants a candidates.read holder, not a
# narrower or wider one invented here. Internal-only identifiers
# (source_blob_path, id) are left out because they're irrelevant to
# answering a question about the candidate, not because they're more
# sensitive than what get_candidate_endpoint already returns.
_CONTEXT_FIELDS = (
    "Name", "JD Match", "Resume Strength Score", "Candidate Type",
    "ranking_tier", "Profile Summary", "Top Matching Keywords",
    "Projects", "Certifications & Courses", "Employment Gaps Detected",
    "HR Notes",
)


def resolve_chat_context(candidate_id: str) -> dict:
    """Authorization itself (does this USER hold candidates.read) is
    resolved by the caller via require_permission() before this is ever
    called -- this function only resolves WHICH DATA a request for
    `candidate_id` may see, identical to get_candidate_endpoint's own
    scope. Raises ChatNotFoundError if candidate_id doesn't resolve to a
    real record; never silently returns a partial/empty context for a
    bad id."""
    candidate = repositories.get_candidate(candidate_id)
    if candidate is None:
        raise ChatNotFoundError(f"Candidate '{candidate_id}' not found.")
    return {field: candidate.get(field) for field in _CONTEXT_FIELDS}


def _sanitize_context(context: dict) -> dict:
    """Every free-text field in the candidate context is resume-derived
    (ultimately candidate-controlled) content -- sanitized before
    interpolation into the chat prompt, same discipline and same
    function as generate_questions.py/interview_bot.py's Task 1/2
    changes (see rag_config.py's docstring for exactly what this does
    and doesn't protect against). Non-string fields (scores, lists,
    booleans) pass through unchanged -- sanitize_for_prompt() only
    operates meaningfully on strings."""
    sanitized = {}
    for key, value in context.items():
        if isinstance(value, str):
            sanitized[key] = sanitize_for_prompt(value)
        elif isinstance(value, list):
            sanitized[key] = [sanitize_for_prompt(v) if isinstance(v, str) else v for v in value]
        else:
            sanitized[key] = value
    return sanitized


def build_chat_prompt(question: str, context: dict) -> str:
    """`question` is the staff user's own free-typed input and `context`
    carries candidate-controlled resume-derived text -- both are
    untrusted and sanitized before interpolation. The model is given
    only this one candidate's already-authorized context; it has no
    tool/function-call capability to fetch anything else, so there is no
    path from this prompt to another candidate's data."""
    sanitized_context = _sanitize_context(context)
    sanitized_question = sanitize_for_prompt(question)
    return f"""
You are a recruiting assistant answering a staff member's question about
ONE specific candidate. Use ONLY the candidate information given below --
never invent facts, and never discuss any candidate other than this one.

Candidate information:
\"\"\"{sanitized_context}\"\"\"

Staff member's question:
\"\"\"{sanitized_question}\"\"\"

Answer concisely and only from the candidate information above. If the
information above doesn't answer the question, say so plainly.
"""
