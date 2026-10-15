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

_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / "context-lake"))
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


@bp.route(route="jds", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_jds_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Multi-JD support (Phase 6): lists every job description currently in
    jd_folder/, so the Recruiter/Admin Dashboard can let a recruiter pick
    which open role to view candidate rankings for. Reads jd_folder/
    directly, same as observability/summary reads logs/ directly — no
    Cosmos DB involved, works with zero Azure resources provisioned.
    Candidates' own per-JD scores live in the "JD Matches" column written
    by ResumeMEtrics.py (see jd_manager.py) — not exposed by this endpoint,
    since that data isn't in candidate.schema.json yet (CSV/scoring_history
    only); filtering candidates by jd_id is follow-up work once it is."""
    sys.path.insert(0, str(_repo_root))
    import jd_manager

    jds = jd_manager.list_jds(_repo_root / "jd_folder")
    return _json_response({
        "count": len(jds),
        "jds": [{"jd_id": jd["jd_id"], "jd_filename": jd["jd_filename"]} for jd in jds],
    })


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

    # Best-effort WhatsApp notification — deliberately OUTSIDE the Cosmos
    # try/except above: the review decision has already been recorded
    # successfully at this point, so a WhatsApp failure (not configured, a
    # Meta API error, a missing/invalid phone number) must not be reported
    # back as if the review itself failed. Logged either way, never raises.
    whatsapp_status = _notify_candidate_whatsapp(candidate, decision)

    return _json_response({
        "candidate_id": candidate_id, "decision": decision, "status": "recorded",
        "whatsapp_notification": whatsapp_status,
    })


def _notify_candidate_whatsapp(candidate: dict, decision: str) -> str:
    phone = candidate.get("Phone")
    if not phone:
        return "skipped: no phone number on file"
    try:
        import whatsapp_client

        status_text = "moved forward" if decision == "approve" else "not moving forward at this time"
        whatsapp_client.send_whatsapp_template(
            phone, "application_status_update",
            parameters=[candidate.get("Name", "Candidate"), status_text],
        )
        return "sent"
    except Exception as e:
        return f"failed: {e}"


@bp.route(route="candidates/{candidate_id}/verify-document", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def verify_document_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Certificate/marksheet verification. Body:
    {"document_base64": "...", "claimed_fields": {"Name": "...", "Degree": "...", ...},
     "fields_to_check": ["Name", "Degree"]}  (fields_to_check defaults to claimed_fields' keys).

    REAL SCOPE NOTE, not papered over: context-lake/schema/candidate.schema.json
    does not capture Degree/CGPA/Institution as structured fields anywhere
    — ResumeParser.py's merge prompt never extracts them — so this
    endpoint cannot read "what the candidate claims" from the existing
    Cosmos candidate record. claimed_fields must be supplied by the
    caller (e.g. a future candidate application form that collects this
    directly), not invented or inferred here.

    Scope per the user's earlier confirmation: internal consistency +
    tamper/forgery signals only, via document_verification.py's
    confidence-flagging (NOT a real forgery detector — see that module's
    docstring). No external institution/board verification.
    """
    candidate_id = req.route_params.get("candidate_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    document_base64 = body.get("document_base64")
    claimed_fields = body.get("claimed_fields") or {}
    fields_to_check = body.get("fields_to_check") or list(claimed_fields.keys())

    if not document_base64:
        return _error_response("'document_base64' is required.", status_code=400)
    if not claimed_fields:
        return _error_response("'claimed_fields' is required (nothing to cross-check against).", status_code=400)

    import base64
    try:
        file_bytes = base64.b64decode(document_base64, validate=True)
    except Exception:
        return _error_response("'document_base64' is not valid base64.", status_code=400)

    try:
        import document_intelligence_client
        extracted = document_intelligence_client.analyze_document(file_bytes)
    except Exception as e:
        return _error_response(
            f"Document Intelligence is not available: {e}. "
            "See docs/azure-provisioning/phase5-document-intelligence-whatsapp.md.",
            status_code=503,
        )

    import document_verification
    extracted_values = {name: f["value"] for name, f in extracted.items()}
    extracted_confidences = {name: f["confidence"] for name, f in extracted.items()}

    cross_check = document_verification.cross_check_document(extracted_values, claimed_fields, fields_to_check)
    flagged = document_verification.confidence_flags(extracted_confidences)
    summary = document_verification.verification_summary(cross_check, flagged)

    try:
        repositories.append_audit_log({
            "candidate_id": candidate_id,
            "action": "document_verification",
            "summary": summary,
            "match_rate": cross_check["match_rate"],
            "mismatches": cross_check["mismatches"],
            "confidence_flagged_fields": flagged,
        })
    except Exception:
        pass  # audit log is best-effort here — the verification result itself is still returned below

    return _json_response({
        "candidate_id": candidate_id,
        "cross_check": cross_check,
        "confidence_flagged_fields": flagged,
        "summary": summary,
    })


@bp.route(route="analytics/executive-summary", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def executive_summary_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Embedded Analytics for Exec Reporting (Phase 6): pipeline-level KPIs
    for an exec dashboard -- candidate volume/tier distribution and the
    human-review approval rate -- distinct from observability/summary
    above, which is LLM token/cost, not hiring-funnel data.

    Approval rate is computed from audit_log entries, not from
    ranking_tier on the live candidate record, because ranking_tier gets
    overwritten on every review decision (see review_candidate_endpoint)
    -- the audit_log is the only place "how many approvals vs rejections
    actually happened" survives as a count, per entry, immutably."""
    try:
        candidates = repositories.list_candidates()
        audit_entries = repositories.list_all_audit_log()
    except Exception as e:
        return _cosmos_unavailable_response(e)

    tier_counts = {}
    for c in candidates:
        tier = c.get("ranking_tier") or "Unscored"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    approvals = sum(1 for e in audit_entries if e.get("action") == "human_review_approve")
    rejections = sum(1 for e in audit_entries if e.get("action") == "human_review_reject")
    total_reviewed = approvals + rejections
    approval_rate = round(approvals / total_reviewed, 4) if total_reviewed else None

    return _json_response({
        "total_candidates": len(candidates),
        "candidates_by_tier": tier_counts,
        "human_review": {
            "approvals": approvals,
            "rejections": rejections,
            "total_reviewed": total_reviewed,
            "approval_rate": approval_rate,
        },
    })


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
