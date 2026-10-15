"""HTTP API layer (Azure Functions HTTP triggers) — the API layer Phase 4's
frontend will call. Built because a gap was found while starting Phase 4:
the roadmap assumed an API layer existed after Phase 2-3, but those phases
only built the data layer (Context Lake) and observability, not anything
HTTP-callable. This closes that gap.

*** NOT YET DEPLOYED OR TESTED AGAINST A RUNNING FUNCTIONS HOST. ***
Same validation level as function_app.py's blob trigger: actually imported
and registered against the real azure-functions SDK (confirms every route/
method is wired correctly — see the bottom of this file's companion check),
but never run under `func start` or deployed, since no Azure Functions Core
Tools are available in this environment.

Error handling is deliberately different from the background pipeline
scripts (ResumeParser.py, ResumeMEtrics.py), which fail soft because they
have a CSV fallback and nobody is waiting synchronously on their output.
An HTTP caller IS waiting on a response — every endpoint that touches
Cosmos DB returns a clear 503 if it's unavailable, rather than silently
succeeding with no/wrong data.

Auth: `auth_level=func.AuthLevel.FUNCTION` (Azure-managed function keys) on
internal/recruiter endpoints — the simplest built-in protection, zero extra
setup. `health` and the candidate-facing `candidates/by-email/{email}`
status check are ANONYMOUS by necessity (candidates don't have a function
key) — see that endpoint's docstring for the real gap this leaves open.
This is NOT the Entra ID RBAC the roadmap calls for eventually; upgrading
to real Entra ID auth is explicit follow-up work, not done here.
"""

import json
import sys
from pathlib import Path

import azure.functions as func

_context_lake_dir = Path(__file__).resolve().parent.parent / "context-lake"
sys.path.insert(0, str(_context_lake_dir))
import repositories  # noqa: E402

bp = func.Blueprint()

# Local dev only — a real frontend origin should replace "*" once one exists.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


def _json_response(data, status_code=200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, default=str),
        status_code=status_code,
        mimetype="application/json",
        headers=CORS_HEADERS,
    )


def _error_response(message: str, status_code=500) -> func.HttpResponse:
    return _json_response({"error": message}, status_code=status_code)


def _cosmos_unavailable_response(exception) -> func.HttpResponse:
    return _error_response(
        f"Context lake (Cosmos DB) is not available: {exception}. "
        "See docs/azure-provisioning/phase2-cosmos-storage-keyvault.md.",
        status_code=503,
    )


@bp.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({"status": "ok"})


@bp.route(route="candidates", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_candidates_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Recruiter/Admin Dashboard: list candidates, optionally filtered by
    ?ranking_tier=Recommended%20for%20Fast-Track (etc)."""
    ranking_tier = req.params.get("ranking_tier")
    try:
        candidates = repositories.list_candidates(ranking_tier=ranking_tier)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(candidates), "candidates": candidates})


@bp.route(route="candidates/{candidate_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_candidate_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    candidate_id = req.route_params.get("candidate_id")
    try:
        candidate = repositories.get_candidate(candidate_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if candidate is None:
        return _error_response(f"Candidate '{candidate_id}' not found.", status_code=404)
    return _json_response(candidate)


@bp.route(route="candidates/{candidate_id}/interviews", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_candidate_interviews_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    candidate_id = req.route_params.get("candidate_id")
    try:
        transcripts = repositories.list_interview_transcripts(candidate_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"candidate_id": candidate_id, "count": len(transcripts), "interviews": transcripts})


@bp.route(route="candidates/by-email/{email}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_candidate_by_email_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Candidate Self-Service Portal status check. ANONYMOUS by necessity —
    candidates don't have a function key. REAL GAP, not fixed here: nothing
    stops one candidate from looking up another candidate's status by
    guessing/knowing their email. A real implementation needs the candidate
    to also supply something private issued at application time (a
    confirmation code/token), not just the email alone. Flagged, not
    silently shipped as if it were secure."""
    email = req.route_params.get("email")
    try:
        candidate = repositories.find_candidate_by_email(email)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if candidate is None:
        return _error_response(f"No application found for '{email}'.", status_code=404)
    # Only status-relevant fields — never the full internal record (HR
    # Notes, Red Flags & Risk Analysis, etc.) to a candidate-facing endpoint.
    safe_fields = {
        "Name": candidate.get("Name"),
        "resume_name": candidate.get("resume_name"),
        "ranking_tier": candidate.get("ranking_tier"),
        "ingested_at": candidate.get("ingested_at"),
    }
    return _json_response(safe_fields)


@bp.route(route="candidates/{candidate_id}/review", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def review_candidate_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Human Review Queue action. Body: {"decision": "approve"|"reject",
    "reviewer": "...", "notes": "..."}. Logs to the immutable audit_log
    (FDE Stage 25 evidence) rather than just overwriting ranking_tier with
    no trace of who decided what or why."""
    candidate_id = req.route_params.get("candidate_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    decision = body.get("decision")
    reviewer = body.get("reviewer")
    notes = body.get("notes", "")

    if decision not in ("approve", "reject"):
        return _error_response("'decision' must be 'approve' or 'reject'.", status_code=400)
    if not reviewer:
        return _error_response("'reviewer' is required.", status_code=400)

    try:
        candidate = repositories.get_candidate(candidate_id)
        if candidate is None:
            return _error_response(f"Candidate '{candidate_id}' not found.", status_code=404)

        candidate["ranking_tier"] = "Recommended for Fast-Track" if decision == "approve" else "Rejected"
        repositories.upsert_candidate(candidate)

        repositories.append_audit_log({
            "candidate_id": candidate_id,
            "action": f"human_review_{decision}",
            "reviewer": reviewer,
            "notes": notes,
        })
    except Exception as e:
        return _cosmos_unavailable_response(e)

    return _json_response({"candidate_id": candidate_id, "decision": decision, "status": "recorded"})


@bp.route(route="observability/summary", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def observability_summary_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Recruiter/Admin Dashboard's cost/token usage view. Doesn't touch
    Cosmos DB at all — reads logs/llm_observability.jsonl directly, so this
    endpoint works today even with zero Azure resources provisioned."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from observability import LOG_PATH

    if not LOG_PATH.exists():
        return _json_response({"total_calls": 0, "total_tokens": 0, "total_cost_usd": 0.0, "by_label": {}})

    total_calls = 0
    total_tokens = 0
    total_cost = 0.0
    by_label = {}

    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            total_calls += 1
            total_tokens += record.get("total_tokens") or 0
            total_cost += record.get("cost_usd") or 0.0
            label = record.get("label", "unknown")
            label_stats = by_label.setdefault(label, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
            label_stats["calls"] += 1
            label_stats["tokens"] += record.get("total_tokens") or 0
            label_stats["cost_usd"] += record.get("cost_usd") or 0.0

    return _json_response({
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 6),
        "by_label": by_label,
    })
