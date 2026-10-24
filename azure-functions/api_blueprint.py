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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import azure.functions as func

_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / "context-lake"))
import repositories  # noqa: E402
import auth as auth_module  # noqa: E402
import observability  # noqa: E402
import azure_integrations  # noqa: E402
import secrets_registry  # noqa: E402
import rag_config  # noqa: E402
import rag_preview  # noqa: E402
import comms_config  # noqa: E402
import email_adapter  # noqa: E402
import model_pricing  # noqa: E402
import persona_catalogue  # noqa: E402
import azure_readiness  # noqa: E402
import llm_utils  # noqa: E402
import az_cli  # noqa: E402
import ephemeral_test_runner  # noqa: E402
import secrets_loader  # noqa: E402

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


def _blob_unavailable_response(exception) -> func.HttpResponse:
    return _error_response(
        f"Blob Storage is not available: {exception}. "
        "See docs/azure-provisioning/phase2-cosmos-storage-keyvault.md.",
        status_code=503,
    )


def _safe_user(user: dict) -> dict:
    """Never return password_hash to any client, ever -- the one and only
    place a user document is translated into something that leaves this
    process. Every endpoint below that returns a user must route it
    through this."""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name"),
        "email": user.get("email"),
        "role_keys": user.get("role_keys", []),
        "status": user.get("status"),
        "created_at": user.get("created_at"),
        "created_by": user.get("created_by"),
        "updated_at": user.get("updated_at"),
    }


def _authenticate(req: func.HttpRequest):
    """Resolves the Authorization header to a real, currently-active user
    -- no permission check yet, just identity. Returns the raw user dict
    (never return it directly to a client -- route through _safe_user())
    or a func.HttpResponse(401) to return immediately.

    This is the ONLY identity input this app trusts: a valid signed JWT
    issued by POST /auth/login after a real bcrypt password check (see
    auth.py) -- never a role or user id the frontend merely claims. The
    user's current status/roles are re-read from the `users`/`roles`
    containers on every call rather than trusted from the token's own
    claims, so a disabled account or a role change takes effect on the
    very next request, not only once a long-lived token expires.
    """
    header = req.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return _error_response(
            "Missing or invalid Authorization header. Log in via POST /auth/login first.", status_code=401,
        )
    token = header[len("Bearer "):].strip()

    import jwt as pyjwt
    try:
        payload = auth_module.verify_token(token)
    except pyjwt.InvalidTokenError:
        return _error_response("Invalid or expired session. Please log in again.", status_code=401)
    except RuntimeError as e:
        return _error_response(str(e), status_code=503)

    try:
        user = repositories.get_user(payload.get("sub"))
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if user is None or user.get("status") != "active":
        return _error_response("This account no longer exists or has been disabled.", status_code=401)
    return user


def require_permission(req: func.HttpRequest, permission_id: str):
    """The real, backend-enforced authorization gate used by every
    protected endpoint below. Returns the authenticated user's RAW dict
    on success (route through _safe_user() before it touches a
    response), or a func.HttpResponse (401/403) to return immediately."""
    user = _authenticate(req)
    if isinstance(user, func.HttpResponse):
        return user
    try:
        permissions = _effective_permissions_for(user)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if permission_id not in permissions:
        return _error_response(
            f"Forbidden: your account doesn't have the '{permission_id}' permission required for this action.",
            status_code=403,
        )
    return user


def require_any_permission(req: func.HttpRequest, permission_ids: list):
    """Same as require_permission(), but passes if the user holds ANY one
    of the given permissions -- used for the couple of endpoints shared
    across roles with no single permission in common (e.g. the
    bulk-decisions list is read by both the recruiter who proposed
    entries, screening.propose, and the admin who approves them,
    screening.approve)."""
    user = _authenticate(req)
    if isinstance(user, func.HttpResponse):
        return user
    try:
        permissions = _effective_permissions_for(user)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if not permissions.intersection(permission_ids):
        return _error_response(
            f"Forbidden: your account needs one of {permission_ids} for this action.", status_code=403,
        )
    return user


@bp.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({"status": "ok"})


@bp.route(route="auth/login", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def login_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """The ONLY real entry point into this app's identity system. Body:
    {"username": "...", "password": "..."}. ANONYMOUS by necessity -- a
    user has no token yet when logging in. Replaces Login.jsx's old
    fully-client-side demo account check; see that file's rewrite."""
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        return _error_response("'username' and 'password' are required.", status_code=400)

    try:
        user = repositories.get_user_by_username(username)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    # Same generic message whether the username doesn't exist or the
    # password is wrong -- never reveal which, that's a username-
    # enumeration leak. Never log the password or the hash anywhere.
    invalid = _error_response("Invalid username or password.", status_code=401)
    if user is None:
        return invalid
    if user.get("status") != "active":
        return _error_response("This account has been disabled.", status_code=403)
    if not auth_module.verify_password(password, user.get("password_hash", "")):
        try:
            repositories.append_audit_log({
                "candidate_id": "_system", "action": "login_failed", "username": username,
            })
        except Exception:
            pass
        return invalid

    try:
        token = auth_module.create_token(user)
    except RuntimeError as e:
        return _error_response(str(e), status_code=503)

    try:
        repositories.append_audit_log({
            "candidate_id": "_system", "action": "login_success", "username": username, "user_id": user["id"],
        })
    except Exception:
        pass

    return _json_response({"token": token, "user": _safe_user(user)})


@bp.route(route="auth/me", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def me_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Lets the frontend restore a session on page load/reload -- resolves
    whatever's in localStorage (the token, nothing else trusted) back to
    real, current account data, rather than the old pattern of trusting a
    {name, roleLabel, view} object a client kept in localStorage as if it
    were authoritative."""
    user = _authenticate(req)
    if isinstance(user, func.HttpResponse):
        return user
    try:
        permissions = _effective_permissions_for(user)
        pages = _effective_pages_for(user)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({
        "user": _safe_user(user), "permissions": sorted(permissions), "pages": sorted(pages),
    })


def _effective_permissions_for(user: dict) -> set:
    role_docs = [r for r in (repositories.get_role(k) for k in user.get("role_keys", [])) if r]
    return auth_module.effective_permissions(role_docs)


def _effective_pages_for(user: dict) -> set:
    """The set of page_ids this user may navigate to -- see
    auth.effective_pages()'s docstring (deny-by-default: a page with no
    policy for any of this user's roles is not included)."""
    policies = repositories.list_all_page_access_policies()
    return auth_module.effective_pages(user.get("role_keys", []), policies)


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
    auth_result = require_permission(req, "jds.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    sys.path.insert(0, str(_repo_root))
    import jd_manager

    jds = jd_manager.list_jds(_repo_root / "jd_folder")
    return _json_response({
        "count": len(jds),
        "jds": [{"jd_id": jd["jd_id"], "jd_filename": jd["jd_filename"], "title": jd["title"]} for jd in jds],
    })


@bp.route(route="candidates", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_candidates_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Recruiter/Admin Dashboard: list candidates, optionally filtered by
    ?ranking_tier=Recommended%20for%20Fast-Track (etc)."""
    auth_result = require_permission(req, "candidates.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    ranking_tier = req.params.get("ranking_tier")
    try:
        candidates = repositories.list_candidates(ranking_tier=ranking_tier)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(candidates), "candidates": candidates})


@bp.route(route="candidates/{candidate_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_candidate_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "candidates.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
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
    auth_result = require_permission(req, "interviews.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
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
    # stage_timeline/pipeline_stages included here (not separate endpoints)
    # because this is the only candidate-facing read today, and it's
    # already scoped to "whoever knows this email" — the same access
    # boundary as everything else returned here, see the docstring's REAL
    # GAP note above. pipeline_stages specifically has to be duplicated
    # here rather than making the candidate call GET /pipeline-config
    # themselves: that endpoint is FUNCTION-auth (recruiter/admin only),
    # and candidates have no function key, same reasoning as every other
    # ANONYMOUS endpoint in this file.
    try:
        pipeline_stages = repositories.get_pipeline_config()
    except Exception:
        pipeline_stages = []

    safe_fields = {
        "Name": candidate.get("Name"),
        "resume_name": candidate.get("resume_name"),
        "ranking_tier": candidate.get("ranking_tier"),
        "ingested_at": candidate.get("ingested_at"),
        "stage_timeline": _stage_timeline_for_candidate(candidate.get("candidate_id")),
        "pipeline_stages": pipeline_stages,
    }
    return _json_response(safe_fields)


def _stage_timeline_for_candidate(candidate_id: str) -> list:
    """[{stage, logged_at}] ordered oldest -> newest — deliberately NOT the
    same shape get_candidate_timeline_endpoint returns to recruiters/admin.
    `notes` and `performance` are the recruiter's internal assessment of
    how the candidate did at each stage (see advance_candidate_stage_endpoint)
    -- stripped here at the API level, not just hidden in the frontend,
    so a candidate inspecting network traffic still can't see them. Same
    "never show internal evaluation to its subject" precedent as HR
    Notes/Red Flags already being excluded from this endpoint above.
    Best-effort: returns [] rather than raising, so a timeline fetch
    failure never breaks a response that has other real data in it
    (candidate status, review result, etc.)."""
    try:
        entries = repositories.list_audit_log(candidate_id)
    except Exception:
        return []
    return [
        {"stage": e.get("stage"), "logged_at": e.get("logged_at")}
        for e in entries if e.get("action") == "stage_update"
    ]


@bp.route(route="applications", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def submit_application_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Candidate Self-Service Portal: Submit a new application. ANONYMOUS
    by necessity, same reasoning as candidates/by-email/{email} above — a
    first-time applicant has no function key.

    Body: {"full_name": "...", "email": "...", "phone": "..." (optional),
    "linkedin": "..." (optional), "jd_id": "..." (optional, see GET /jds),
    "cover_note": "..." (optional), "resume_base64": "...",
    "resume_filename": "something.pdf"}.

    Uploads the resume to the raw-resumes Blob container. The existing
    blob-triggered function (function_app.py's resume_blob_trigger, built
    in Phase 3) picks it up automatically and runs the normal
    ResumeParser.py pipeline — no parsing logic is duplicated here. The
    response is a receipt confirming the file was received, not a parsed
    result; ResumeParser.py runs asynchronously after this returns 202.

    Every field here is recorded in the audit_log entry below (so there's
    a record of what the applicant actually claimed/submitted), but only
    full_name/email/phone have any real meaning downstream today, and
    even those are NOT written into the candidate record —
    ResumeParser.py's own LLM parsing extracts Name/Email/Phone fresh
    from the resume content, which stays the only source of truth for
    that data. jd_id and cover_note are REAL GAPS, not silently faked:
    nothing in ResumeParser.py/ResumeMEtrics.py reads jd_id to target
    scoring at one specific JD (see jd_manager.py — candidates are scored
    against every active JD, not a chosen one), and nothing reads
    cover_note at all yet. Both are captured for a human reviewer to read
    in the audit log, not acted on by the pipeline.
    """
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    full_name = (body.get("full_name") or "").strip()
    email = (body.get("email") or "").strip()
    phone = (body.get("phone") or "").strip() or None
    linkedin = (body.get("linkedin") or "").strip() or None
    jd_id = (body.get("jd_id") or "").strip() or None
    cover_note = (body.get("cover_note") or "").strip() or None
    resume_base64 = body.get("resume_base64")
    resume_filename = (body.get("resume_filename") or "").strip()

    if not full_name:
        return _error_response("'full_name' is required.", status_code=400)
    if not email:
        return _error_response("'email' is required.", status_code=400)
    if not resume_base64:
        return _error_response("'resume_base64' is required.", status_code=400)
    if not resume_filename.lower().endswith(".pdf"):
        return _error_response(
            "'resume_filename' must end in '.pdf' — the processing pipeline only picks up PDFs.",
            status_code=400,
        )

    # Strip any path components the caller might (deliberately or not)
    # have included — the filename becomes a blob path, so this is the
    # same defense as a file-upload endpoint sanitizing a path.
    safe_filename = os.path.basename(resume_filename)

    import base64
    try:
        file_bytes = base64.b64decode(resume_base64, validate=True)
    except Exception:
        return _error_response("'resume_base64' is not valid base64.", status_code=400)

    try:
        import blob_store
        blob_store.upload_resume_bytes("raw-resumes", safe_filename, file_bytes)
    except Exception as e:
        return _blob_unavailable_response(e)

    from candidate_id import candidate_id_for
    cid = candidate_id_for(safe_filename)

    try:
        repositories.append_audit_log({
            "candidate_id": cid,
            "action": "application_submitted",
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "linkedin": linkedin,
            "jd_id": jd_id,
            "cover_note": cover_note,
            "resume_filename": safe_filename,
        })
    except Exception:
        pass  # best-effort — the upload itself already succeeded, which is what actually matters here

    return _json_response({
        "candidate_id": cid,
        "status": "submitted",
        "message": "Your application was received and is being processed. Check back in a few minutes using your email.",
    }, status_code=202)


@bp.route(route="candidates/{candidate_id}/review", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def review_candidate_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Human Review Queue action. Body: {"decision": "approve"|"reject",
    "reviewer": "...", "notes": "..."}. Logs to the immutable audit_log
    (FDE Stage 25 evidence) rather than just overwriting ranking_tier with
    no trace of who decided what or why."""
    auth_result = require_permission(req, "candidates.update")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
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
    """Sends the real WhatsApp status-update template, gated by three
    real checks the Communications Configuration Console now controls:
    (1) the channel's `enabled` flag (comms_config.get_comms_runtime_config()
    -- fails soft to "enabled" if unconfigured, today's behavior);
    (2) duplicate-send protection, so a retried request within the
    configured window doesn't re-notify a candidate who was already
    notified; (3) every attempt (sent or failed) is recorded to the real
    delivery log, not just returned as a string to this call's own
    caller."""
    import whatsapp_client

    phone = candidate.get("Phone")
    candidate_id = candidate.get("candidate_id") or candidate.get("id")
    workflow = "candidate_decision_notification"
    if not phone:
        return "skipped: no phone number on file"

    runtime_cfg = comms_config.get_comms_runtime_config()
    if not runtime_cfg["whatsapp"].get("enabled", True):
        return "skipped: WhatsApp channel is disabled in Communications Configuration"

    window = runtime_cfg["delivery_policies"].get("duplicate_send_window_minutes", 60)
    try:
        if candidate_id and repositories.has_recent_delivery(candidate_id, "whatsapp", workflow, window):
            return "skipped: a notification for this candidate was already sent within the duplicate-send window"
    except Exception:
        pass  # dedup check is best-effort -- never blocks a real notification if Cosmos is unavailable

    status_text = "moved forward" if decision == "approve" else "not moving forward at this time"
    submitted_at = datetime.now(timezone.utc).isoformat()
    try:
        response = whatsapp_client.send_whatsapp_template(
            phone, "application_status_update",
            parameters=[candidate.get("Name", "Candidate"), status_text],
        )
        outcome, detail, result = "provider_accepted", "Meta Graph API accepted the message.", "sent"
    except Exception as e:
        outcome, detail, result = "failed", azure_integrations.redact_secret(str(e)), f"failed: {e}"

    try:
        repositories.record_comms_delivery({
            "correlation_id": candidate_id or "_unknown", "channel": "whatsapp", "provider": "meta_graph_api",
            "workflow": workflow, "submitted_at": submitted_at, "outcome": outcome, "detail": detail,
        })
    except Exception:
        pass  # delivery logging is best-effort -- never blocks or fails the real send
    return result


@bp.route(route="bulk-decisions", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def propose_bulk_decision_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Recruiter proposes a bulk accept/reject on a batch of screening-
    phase candidates. Body: {"decision": "approve"|"reject",
    "candidate_ids": ["...", ...], "proposed_by": "...", "notes": "..." (optional)}.

    Does NOT touch any candidate record -- the AI/scoring pipeline only
    ever produces a recommendation (ranking_tier), and a recruiter acting
    in bulk doesn't finalize anything either. This just records a pending
    proposal; only approve_bulk_decision_endpoint (admin) actually applies
    it. Contrast with review_candidate_endpoint, which takes effect
    immediately -- that's a recruiter's individual decision on one
    candidate, already a real human-in-the-loop action, not an AI output
    being rubber-stamped in bulk."""
    auth_result = require_permission(req, "screening.propose")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    decision = body.get("decision")
    candidate_ids = body.get("candidate_ids")
    proposed_by = body.get("proposed_by")
    notes = body.get("notes", "")

    if decision not in ("approve", "reject"):
        return _error_response("'decision' must be 'approve' or 'reject'.", status_code=400)
    if not candidate_ids or not isinstance(candidate_ids, list):
        return _error_response("'candidate_ids' must be a non-empty list.", status_code=400)
    if not proposed_by:
        return _error_response("'proposed_by' is required.", status_code=400)

    try:
        doc = repositories.create_bulk_decision(decision, candidate_ids, proposed_by, notes)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    return _json_response({
        "id": doc["id"], "decision": decision, "candidate_count": len(candidate_ids),
        "status": "pending", "message": "Proposal recorded — no candidates changed yet. An admin must approve it first.",
    }, status_code=202)


@bp.route(route="bulk-decisions", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_bulk_decisions_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Admin's approval queue -- also read by recruiters to see the status
    of their own proposals (RecruiterReports.jsx). ?status=pending|
    approved|rejected to filter, omit for everything (useful for an audit
    trail of past decisions too)."""
    auth_result = require_any_permission(req, ["screening.propose", "screening.approve"])
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    status = req.params.get("status")
    try:
        decisions = repositories.list_bulk_decisions(status=status)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(decisions), "decisions": decisions})


def _apply_bulk_decision(decision_id: str, decision: str, candidate_ids: list, reviewed_by: str, proposal_notes: str) -> list:
    """Applies an approved bulk decision to every candidate in it -- same
    tier-change + audit_log + best-effort WhatsApp notify as
    review_candidate_endpoint does for one candidate, just looped.
    Returns a per-candidate result list so the caller can report
    partial failures (one bad candidate_id doesn't abort the rest)."""
    results = []
    for candidate_id in candidate_ids:
        try:
            candidate = repositories.get_candidate(candidate_id)
            if candidate is None:
                results.append({"candidate_id": candidate_id, "status": "skipped: not found"})
                continue
            candidate["ranking_tier"] = "Recommended for Fast-Track" if decision == "approve" else "Rejected"
            repositories.upsert_candidate(candidate)
            repositories.append_audit_log({
                "candidate_id": candidate_id,
                "action": f"human_review_{decision}",
                "reviewer": f"bulk_decision:{decision_id} (approved by {reviewed_by})",
                "notes": proposal_notes,
            })
            whatsapp_status = _notify_candidate_whatsapp(candidate, decision)
            results.append({"candidate_id": candidate_id, "status": "applied", "whatsapp_notification": whatsapp_status})
        except Exception as e:
            results.append({"candidate_id": candidate_id, "status": f"failed: {e}"})
    return results


@bp.route(route="bulk-decisions/{decision_id}/approve", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def approve_bulk_decision_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Admin approves a pending bulk proposal -- this is the ONLY place a
    bulk accept/reject actually takes effect on candidate records. Body:
    {"reviewed_by": "..."}."""
    auth_result = require_permission(req, "screening.approve")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    decision_id = req.route_params.get("decision_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    reviewed_by = body.get("reviewed_by")
    if not reviewed_by:
        return _error_response("'reviewed_by' is required.", status_code=400)

    try:
        pending = repositories.get_bulk_decision(decision_id)
        if pending is None:
            return _error_response(f"Bulk decision '{decision_id}' not found.", status_code=404)
        if pending["status"] != "pending":
            return _error_response(f"This proposal is already '{pending['status']}' — it can't be approved again.", status_code=409)

        results = _apply_bulk_decision(
            decision_id, pending["decision"], pending["candidate_ids"], reviewed_by, pending.get("notes", ""),
        )
        repositories.mark_bulk_decision_reviewed(decision_id, "approved", reviewed_by, extra={"results": results})
    except ValueError as e:
        status = 404 if "not found" in str(e) else 409
        return _error_response(str(e), status_code=status)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    return _json_response({"id": decision_id, "status": "approved", "results": results})


@bp.route(route="bulk-decisions/{decision_id}/reject", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def reject_bulk_decision_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Admin rejects a pending bulk proposal -- no candidate is touched,
    the proposal is just marked rejected (kept for the audit trail, not
    deleted). Body: {"reviewed_by": "...", "notes": "..." (optional, why
    it was rejected)}."""
    auth_result = require_permission(req, "screening.approve")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    decision_id = req.route_params.get("decision_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    reviewed_by = body.get("reviewed_by")
    if not reviewed_by:
        return _error_response("'reviewed_by' is required.", status_code=400)

    try:
        repositories.mark_bulk_decision_reviewed(
            decision_id, "rejected", reviewed_by, extra={"review_notes": body.get("notes", "")},
        )
    except ValueError as e:
        status = 404 if "not found" in str(e) else 409
        return _error_response(str(e), status_code=status)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    return _json_response({"id": decision_id, "status": "rejected"})


@bp.route(route="pipeline-config", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_pipeline_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """The recruiter-configurable ordered list of hiring-process stage
    names (Shortlisted, Aptitude Exam, ... Selected/Rejected). One global
    list for now, not per-JD -- see repositories.get_pipeline_config()'s
    docstring. Defaults to DEFAULT_PIPELINE_STAGES if nobody's configured
    one yet, so this never 404s."""
    auth_result = require_permission(req, "interviews.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        stages = repositories.get_pipeline_config()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"stages": stages})


@bp.route(route="pipeline-config", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def update_pipeline_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Recruiter edits the stage list. Body: {"stages": ["Shortlisted", ...]}
    -- a full ordered replace, not a diff (same semantics as upsert_candidate).
    Existing candidates already past a stage that gets removed keep their
    recorded history (audit_log is immutable) -- only future stage-advance
    calls are validated against the new list."""
    auth_result = require_permission(req, "interviews.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    stages = body.get("stages")
    if not isinstance(stages, list) or not stages:
        return _error_response("'stages' must be a non-empty list of stage names.", status_code=400)
    if not all(isinstance(s, str) and s.strip() for s in stages):
        return _error_response("Every stage name must be a non-empty string.", status_code=400)

    try:
        repositories.set_pipeline_config(stages)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"stages": stages})


@bp.route(route="candidates/{candidate_id}/timeline", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_candidate_timeline_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Recruiter/Admin view of one candidate's hiring-stage history --
    every stage_update audit_log entry for them, oldest first. Current
    stage is the last entry, if any (nobody advanced yet = no entries,
    not an error)."""
    auth_result = require_permission(req, "interviews.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    candidate_id = req.route_params.get("candidate_id")
    try:
        entries = repositories.list_audit_log(candidate_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    timeline = [
        {
            "stage": e.get("stage"), "updated_by": e.get("updated_by"), "notes": e.get("notes"),
            "performance": e.get("performance"), "logged_at": e.get("logged_at"),
        }
        for e in entries if e.get("action") == "stage_update"
    ]
    return _json_response({"candidate_id": candidate_id, "timeline": timeline})


PERFORMANCE_RATINGS = ["Excellent", "Good", "Average", "Poor"]


@bp.route(route="candidates/{candidate_id}/stage", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def advance_candidate_stage_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Recruiter/Admin sets a candidate's current hiring stage AND records
    how they did there. Body: {"stage": "Technical Exam", "updated_by": "...",
    "notes": "...", "performance": "Good" (optional, one of PERFORMANCE_RATINGS)}.

    This is the real point of the stage timeline, not just a progress
    tracker: a recruiter isn't only approving/rejecting a resume once,
    they're evaluating a candidate's performance at each individual round
    (exam score, interview impression, etc.) -- 'notes' is REQUIRED for
    that reason, not optional metadata; recording that someone reached
    "Technical Exam" with no assessment of how it went defeats the
    purpose. 'performance' is an optional structured rating alongside the
    free-text notes, when the recruiter wants something scannable across
    a candidate's whole history without reading every note.

    'stage' must be one of the currently configured pipeline-config
    stages -- not free text -- so the timeline can't drift from whatever
    the recruiter actually configured (a typo'd stage name would
    otherwise silently create an orphan stage nothing else recognizes).

    NO SKIPPING: 'stage' must be the immediate next stage after whatever
    this candidate's current stage is (or the first configured stage if
    they haven't started) -- a recruiter can send a candidate to the next
    round, never jump several rounds ahead in one call. This also means
    there's no "moving backward" despite this docstring's note below
    about history surviving backward moves -- that refers to what
    happens if the stage *list* is reordered later, not to this endpoint
    allowing an out-of-order call.

    Appends one immutable stage_update audit_log entry; does not
    overwrite or remove any earlier entry, so the full history (which
    stages this candidate actually passed through, and when) survives
    even if the config changes later.

    notes/performance are NEVER returned to the candidate-facing
    endpoint (candidates/by-email/{email}) -- see
    _stage_timeline_for_candidate()'s docstring."""
    auth_result = require_permission(req, "interviews.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    candidate_id = req.route_params.get("candidate_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    stage = body.get("stage")
    updated_by = body.get("updated_by")
    notes = body.get("notes")
    performance = body.get("performance")

    if not stage:
        return _error_response("'stage' is required.", status_code=400)
    if not updated_by:
        return _error_response("'updated_by' is required.", status_code=400)
    if not notes or not notes.strip():
        return _error_response("'notes' is required -- record how the candidate actually did at this stage.", status_code=400)
    if performance and performance not in PERFORMANCE_RATINGS:
        return _error_response(f"'performance' must be one of {PERFORMANCE_RATINGS} if given.", status_code=400)

    try:
        candidate = repositories.get_candidate(candidate_id)
        if candidate is None:
            return _error_response(f"Candidate '{candidate_id}' not found.", status_code=404)

        configured_stages = repositories.get_pipeline_config()
        if stage not in configured_stages:
            return _error_response(
                f"'{stage}' is not a configured pipeline stage. Configured stages: {configured_stages}.",
                status_code=400,
            )

        # No skipping ahead -- a recruiter can only send a candidate to the
        # immediate next configured stage, never jump several rounds at
        # once. current_stage is whatever the last stage_update recorded
        # (None if they haven't started), and the only valid 'stage' value
        # is configured_stages[current_index + 1] (or configured_stages[0]
        # if they haven't started yet).
        past_entries = [e for e in repositories.list_audit_log(candidate_id) if e.get("action") == "stage_update"]
        current_stage = past_entries[-1]["stage"] if past_entries else None

        if current_stage is None:
            expected_next = configured_stages[0]
        elif current_stage not in configured_stages:
            return _error_response(
                f"Candidate's current stage '{current_stage}' is no longer in the configured pipeline "
                "(the stage list was changed after they were placed there) -- fix the pipeline config "
                "before advancing this candidate further.",
                status_code=409,
            )
        else:
            current_index = configured_stages.index(current_stage)
            if current_index == len(configured_stages) - 1:
                return _error_response(
                    f"'{current_stage}' is already the final configured stage — there's nothing to advance to.",
                    status_code=400,
                )
            expected_next = configured_stages[current_index + 1]

        if stage != expected_next:
            return _error_response(
                f"Candidates can only be advanced one stage at a time, in order. Current stage: "
                f"{current_stage or '(not started)'}. Next allowed stage: '{expected_next}'. Got: '{stage}'.",
                status_code=400,
            )

        repositories.append_audit_log({
            "candidate_id": candidate_id, "action": "stage_update",
            "stage": stage, "updated_by": updated_by, "notes": notes, "performance": performance,
        })
    except Exception as e:
        return _cosmos_unavailable_response(e)

    return _json_response({"candidate_id": candidate_id, "stage": stage, "status": "recorded"})


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
    auth_result = require_permission(req, "documents.verify")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
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
    auth_result = require_permission(req, "analytics.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
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


@bp.route(route="manage/reports/resume-processing", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def resume_processing_report_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Resume Processing Report (CDPI Product Personas & Reporting doc,
    Phase 1 #5) -- built from the REAL document_extraction stage spans
    already recorded in logs/ai_traces.jsonl by the AI Observability
    Center, not a separately-maintained processing counter (this app's
    "avoid duplicate reporting logic" requirement). Distinguishes
    succeeded from failed extraction attempts, with real failure detail
    (never fabricated). There is no queue/backlog concept in this repo's
    batch-script pipeline (ResumeParser.py processes a folder, it
    doesn't dequeue work) -- `pending_queue_depth` is honestly null,
    not invented."""
    auth_result = require_permission(req, "observability.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    records = _read_jsonl(observability.TRACE_LOG_PATH)
    extraction_spans = [r for r in records if r.get("stage") == "document_extraction"]
    failed = [r for r in extraction_spans if r.get("status") == "failed"]
    succeeded = sum(1 for r in extraction_spans if r.get("status") == "succeeded")
    return _json_response({
        "total_processing_attempts": len(extraction_spans),
        "succeeded": succeeded,
        "failed_count": len(failed),
        "failure_rate": round(len(failed) / len(extraction_spans), 4) if extraction_spans else None,
        "recent_failures": [
            {"correlation_id": r.get("correlation_id"), "error": r.get("error"), "logged_at": r.get("logged_at")}
            for r in failed[-20:]
        ],
        "pending_queue_depth": None,
        "data_source": "logs/ai_traces.jsonl (document_extraction stage spans, AI Observability Center)",
    })


@bp.route(route="manage/reports/communication-delivery", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def communication_delivery_report_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Communication Delivery Report (CDPI Product Personas & Reporting
    doc, Phase 1 #6) -- real delivery records from comms_delivery_log
    (see _notify_candidate_whatsapp above), never fabricated statistics.
    Only WhatsApp notifications (the candidate-decision notification
    workflow) are logged anywhere in this repo today -- email has NO
    delivery logging at all yet, not the legacy invite/report scripts,
    not even admin-triggered test sends -- a real, stated gap, not a
    zero-volume coincidence."""
    auth_result = require_permission(req, "comms.delivery.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        deliveries = repositories.list_all_comms_deliveries(limit=500)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    by_channel = {}
    for d in deliveries:
        channel = d.get("channel", "unknown")
        stats = by_channel.setdefault(channel, {"submitted": 0, "provider_accepted": 0, "failed": 0})
        stats["submitted"] += 1
        if d.get("outcome") == "provider_accepted":
            stats["provider_accepted"] += 1
        elif d.get("outcome") == "failed":
            stats["failed"] += 1

    return _json_response({
        "count": len(deliveries), "by_channel": by_channel, "recent": deliveries[:50],
        "note": "Only WhatsApp notifications are logged here -- email delivery logging does not exist yet anywhere in this repo (see this endpoint's docstring).",
    })


@bp.route(route="manage/personas", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def persona_catalogue_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """The 12-persona representative-configuration catalogue (CDPI Product
    Personas doc, Section 8). Gated by roles.read -- the same permission
    that already gates viewing what any role grants, since this endpoint
    is a read-only view DERIVED from auth.DEFAULT_ROLES/PAGES, not a
    separate authorization surface (see persona_catalogue.py's docstring).
    Returns persona_catalogue.validate_persona_catalogue()'s result too,
    so a future permission-grant drift (a role losing a permission an
    'available' report depends on) is visible here rather than silently
    breaking."""
    auth_result = require_permission(req, "roles.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    return _json_response({
        "personas": persona_catalogue.describe_all_personas(),
        "validation_problems": persona_catalogue.validate_persona_catalogue(),
    })


@bp.route(route="audit-log", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_audit_log_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Applicant Pipeline view (Phase 6+): the raw audit_log, optionally
    filtered by ?action=. One cross-partition read instead of one call
    per candidate -- repositories.list_all_audit_log() already exists
    (built for executive_summary_endpoint above), this just exposes it
    directly so the frontend can compute each candidate's furthest known
    stage (Applied / Parsed & Scored / Document Verified / Reviewed)
    client-side from the full entry list, polled periodically rather
    than a true push-based live feed -- there's no websocket/SignalR
    layer here, see Pipeline.jsx's doc comment for why that's an honest
    label, not "real-time" overclaimed."""
    auth_result = require_permission(req, "audit.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    action = req.params.get("action")
    try:
        entries = repositories.list_all_audit_log(action=action)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(entries), "entries": entries})


def _read_jsonl(path) -> list:
    """Shared by observability_summary_endpoint and the AI Observability
    Center endpoints below -- one place that reads a JSON-lines log file,
    skipping blank/corrupt lines, instead of each endpoint re-implementing
    the same loop (this feature's "avoid duplicate implementations"
    requirement)."""
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _llm_observability_summary() -> dict:
    records = _read_jsonl(observability.LOG_PATH)
    total_calls = len(records)
    total_tokens = 0
    total_cost = 0.0
    by_label = {}
    for record in records:
        total_tokens += record.get("total_tokens") or 0
        total_cost += record.get("cost_usd") or 0.0
        label = record.get("label", "unknown")
        label_stats = by_label.setdefault(label, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
        label_stats["calls"] += 1
        label_stats["tokens"] += record.get("total_tokens") or 0
        label_stats["cost_usd"] += record.get("cost_usd") or 0.0
    return {
        "total_calls": total_calls, "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 6), "by_label": by_label,
    }


@bp.route(route="observability/summary", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def observability_summary_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Recruiter/Admin Dashboard's cost/token usage view. Doesn't touch
    Cosmos DB at all — reads logs/llm_observability.jsonl directly, so this
    endpoint works today even with zero Azure resources provisioned."""
    auth_result = require_permission(req, "analytics.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    return _json_response(_llm_observability_summary())


# ---------------------------------------------------------------------------
# Admin Panel: User Management (/admin/users*) and Role & Permission
# Management (/admin/roles*, /admin/permissions). Every endpoint below is
# gated by require_permission() -- there is no separate/looser path into
# any of this; the frontend's UserManagement.jsx/RoleManagement.jsx call
# these same endpoints, nothing is client-side-only.
# ---------------------------------------------------------------------------
def _audit_admin_action(action: str, actor: dict, **fields) -> None:
    """Admin actions get their own audit_log trail, partitioned under a
    fixed '_system' candidate_id (these aren't about any one candidate).
    Never includes a password or password_hash in any field -- every
    call site below passes only ids/usernames/role_keys."""
    try:
        repositories.append_audit_log({
            "candidate_id": "_system", "action": action,
            "actor_id": actor["id"], "actor_username": actor["username"],
            **fields,
        })
    except Exception:
        pass


@bp.route(route="manage/permissions", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_permissions_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """The full permission catalog, grouped by module -- powers the Roles
    & Permissions admin page's checkbox grid. Requires roles.read, same
    as viewing roles themselves (the catalog is read-only reference data
    either way)."""
    auth_result = require_permission(req, "roles.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    return _json_response({"permissions": auth_module.PERMISSIONS})


@bp.route(route="manage/roles", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_roles_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "roles.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        roles = repositories.list_roles()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(roles), "roles": roles})


@bp.route(route="manage/roles", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def create_role_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Create a new CUSTOM role. Body: {"role_key": "hiring_manager",
    "display_name": "Hiring Manager", "description": "...",
    "permission_ids": ["candidates.read", ...]}.

    Requires roles.create -- deliberately a DIFFERENT permission from
    roles.assign (granting an existing role to a user), so Admin (which
    only has roles.assign) cannot define what a role grants, only Super
    Admin (which has both) can -- this is the concrete difference
    between the two roles the spec called for."""
    auth_result = require_permission(req, "roles.create")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    role_key = (body.get("role_key") or "").strip().lower().replace(" ", "_")
    display_name = (body.get("display_name") or "").strip()
    description = (body.get("description") or "").strip()
    permission_ids = body.get("permission_ids") or []

    if not role_key or not display_name:
        return _error_response("'role_key' and 'display_name' are required.", status_code=400)
    unknown = [p for p in permission_ids if p not in auth_module.PERMISSIONS]
    if unknown:
        return _error_response(f"Unknown permission id(s): {unknown}.", status_code=400)

    try:
        if repositories.get_role(role_key) is not None:
            return _error_response(f"Role '{role_key}' already exists.", status_code=409)
        doc = repositories.create_role(role_key, display_name, description, permission_ids, is_system=False)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("role_created", actor, role_key=role_key, permission_ids=permission_ids)
    return _json_response(doc, status_code=201)


@bp.route(route="manage/roles/{role_key}", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def update_role_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Edit a role's permission grants. Body: {"display_name": "..."
    (optional), "description": "..." (optional), "permission_ids": [...] (optional)}.

    Requires roles.update. System roles (Admin, Recruiter,
    Interviewer, Candidate) are READ-ONLY here -- their grants are the
    deliberately-designed least-privilege baseline the spec called for,
    and editing them in place (e.g. quietly widening Recruiter to include
    screening.approve) would silently break the separation the whole
    bulk-decision-approval feature depends on. Only custom roles created
    via POST /admin/roles can be edited."""
    auth_result = require_permission(req, "roles.update")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    role_key = req.route_params.get("role_key")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    try:
        role = repositories.get_role(role_key)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if role is None:
        return _error_response(f"Role '{role_key}' not found.", status_code=404)
    if role.get("is_system"):
        return _error_response(
            f"'{role_key}' is a built-in system role and can't be edited. Create a custom role instead.",
            status_code=403,
        )

    if "permission_ids" in body:
        unknown = [p for p in body["permission_ids"] if p not in auth_module.PERMISSIONS]
        if unknown:
            return _error_response(f"Unknown permission id(s): {unknown}.", status_code=400)
        role["permission_ids"] = body["permission_ids"]
    if "display_name" in body and body["display_name"]:
        role["display_name"] = body["display_name"]
    if "description" in body:
        role["description"] = body["description"]

    try:
        doc = repositories.update_role(role)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("role_updated", actor, role_key=role_key)
    return _json_response(doc)


@bp.route(route="manage/users", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_users_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "users.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        users = repositories.list_users()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(users), "users": [_safe_user(u) for u in users]})


@bp.route(route="manage/users/{user_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_user_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "users.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    user_id = req.route_params.get("user_id")
    try:
        user = repositories.get_user(user_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if user is None:
        return _error_response(f"User '{user_id}' not found.", status_code=404)
    return _json_response(_safe_user(user))


@bp.route(route="manage/users", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def create_user_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Create a new user account. Body: {"username": "...", "password": "...",
    "display_name": "...", "email": "...", "role_keys": ["recruiter"]}.

    Requires users.create. The password is hashed with bcrypt immediately
    and the plaintext is never stored, logged, or echoed back in the
    response -- see auth.hash_password(). Admin is the top-level role in
    this app (no separate Super Admin tier) -- granting the admin role at
    creation time still requires the ACTOR to already hold admin
    themselves, so a lesser custom role that happens to combine
    users.create with roles.assign still can't mint a brand-new peer
    Admin. Same rule, same reasoning, applied in PUT .../roles below."""
    auth_result = require_permission(req, "users.create")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    display_name = (body.get("display_name") or "").strip()
    email = (body.get("email") or "").strip()
    role_keys = body.get("role_keys") or []

    if not username or not password or not display_name:
        return _error_response("'username', 'password', and 'display_name' are required.", status_code=400)
    if len(password) < 8:
        return _error_response("'password' must be at least 8 characters.", status_code=400)
    if not isinstance(role_keys, list):
        return _error_response("'role_keys' must be a list.", status_code=400)

    try:
        actor_role_docs = [r for r in (repositories.get_role(k) for k in actor.get("role_keys", [])) if r]
    except Exception as e:
        return _cosmos_unavailable_response(e)
    actor_is_admin = "admin" in {r["role_key"] for r in actor_role_docs}
    if "admin" in role_keys and not actor_is_admin:
        return _error_response(
            "Only an existing Admin can grant the Admin role to a user.", status_code=403,
        )

    try:
        if repositories.get_user_by_username(username) is not None:
            return _error_response(f"Username '{username}' is already taken.", status_code=409)
        for key in role_keys:
            if repositories.get_role(key) is None:
                return _error_response(f"Unknown role '{key}'.", status_code=400)
        password_hash = auth_module.hash_password(password)
        user = repositories.create_user(username, password_hash, display_name, email, role_keys, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("user_created", actor, target_user_id=user["id"], target_username=username, role_keys=role_keys)
    return _json_response(_safe_user(user), status_code=201)


@bp.route(route="manage/users/{user_id}", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def update_user_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Edit a user's profile and/or reset their password. Body (all
    optional): {"display_name": "...", "email": "...", "password": "..."}.
    Requires users.update. Role changes go through PUT .../roles below
    (a separate endpoint/permission boundary: roles.assign), not here."""
    auth_result = require_permission(req, "users.update")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    user_id = req.route_params.get("user_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    try:
        user = repositories.get_user(user_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if user is None:
        return _error_response(f"User '{user_id}' not found.", status_code=404)

    if "display_name" in body and body["display_name"]:
        user["display_name"] = body["display_name"]
    if "email" in body:
        user["email"] = body["email"]
    if body.get("password"):
        if len(body["password"]) < 8:
            return _error_response("'password' must be at least 8 characters.", status_code=400)
        user["password_hash"] = auth_module.hash_password(body["password"])

    try:
        updated = repositories.update_user(user)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("user_updated", actor, target_user_id=user_id, fields=list(body.keys()))
    return _json_response(_safe_user(updated))


@bp.route(route="manage/users/{user_id}/status", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def set_user_status_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Enable/disable a user account. Body: {"status": "active"|"disabled"}.
    Requires users.disable.

    Administrative safeguard (explicit spec requirement): a user cannot
    disable their own account (self-lockout prevention, and incidentally
    also blocks a self-escalation vector where a user disables themselves
    to dodge an audit trail), and the last ACTIVE account holding the
    admin role (the top-level role in this app) can never be disabled --
    there must always be at least one active Admin who can fix a
    misconfiguration."""
    auth_result = require_permission(req, "users.disable")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    user_id = req.route_params.get("user_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    new_status = body.get("status")
    if new_status not in ("active", "disabled"):
        return _error_response("'status' must be 'active' or 'disabled'.", status_code=400)

    if new_status == "disabled" and user_id == actor["id"]:
        return _error_response("You cannot disable your own account.", status_code=403)

    try:
        user = repositories.get_user(user_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if user is None:
        return _error_response(f"User '{user_id}' not found.", status_code=404)

    if new_status == "disabled" and "admin" in user.get("role_keys", []):
        try:
            remaining = repositories.count_active_users_with_role("admin", exclude_user_id=user_id)
        except Exception as e:
            return _cosmos_unavailable_response(e)
        if remaining < 1:
            return _error_response(
                "Can't disable the last active Admin account. Promote another account to Admin first.",
                status_code=409,
            )

    user["status"] = new_status
    try:
        updated = repositories.update_user(user)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("user_status_changed", actor, target_user_id=user_id, new_status=new_status)
    return _json_response(_safe_user(updated))


@bp.route(route="manage/users/{user_id}/roles", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def set_user_roles_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Replace a user's role assignments. Body: {"role_keys": ["recruiter", "interviewer"]}.
    Requires roles.assign.

    Administrative safeguards (explicit spec requirements):
    - NO SELF-ESCALATION: a user cannot change their own role assignments
      at all, full stop -- not even an actor who already holds
      roles.assign can grant themselves a new role via this endpoint.
    - Granting or keeping the admin role (the top-level role in this app)
      for someone else requires the ACTOR to already be an Admin
      themselves (a lesser custom role with roles.assign cannot mint a
      peer Admin).
    - PROTECT THE LAST ADMIN: removing admin from the last active account
      that holds it is blocked, same rule as disabling that account
      above."""
    auth_result = require_permission(req, "roles.assign")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    user_id = req.route_params.get("user_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    role_keys = body.get("role_keys")
    if not isinstance(role_keys, list):
        return _error_response("'role_keys' must be a list.", status_code=400)

    if user_id == actor["id"]:
        return _error_response("You cannot change your own role assignments.", status_code=403)

    try:
        user = repositories.get_user(user_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if user is None:
        return _error_response(f"User '{user_id}' not found.", status_code=404)

    try:
        for key in role_keys:
            if repositories.get_role(key) is None:
                return _error_response(f"Unknown role '{key}'.", status_code=400)
        actor_role_docs = [r for r in (repositories.get_role(k) for k in actor.get("role_keys", [])) if r]
    except Exception as e:
        return _cosmos_unavailable_response(e)
    actor_is_admin = "admin" in {r["role_key"] for r in actor_role_docs}

    had_admin = "admin" in user.get("role_keys", [])
    will_have_admin = "admin" in role_keys
    if will_have_admin and not had_admin and not actor_is_admin:
        return _error_response("Only an existing Admin can grant the Admin role.", status_code=403)

    if had_admin and not will_have_admin:
        try:
            remaining = repositories.count_active_users_with_role("admin", exclude_user_id=user_id)
        except Exception as e:
            return _cosmos_unavailable_response(e)
        if remaining < 1:
            return _error_response(
                "Can't remove Admin from the last active account that holds it. "
                "Promote another account to Admin first.",
                status_code=409,
            )

    user["role_keys"] = role_keys
    try:
        updated = repositories.update_user(user)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("roles_assigned", actor, target_user_id=user_id, role_keys=role_keys)
    return _json_response(_safe_user(updated))


# ---------------------------------------------------------------------------
# Dynamic Page Access Management (/admin/pages*). See auth.PAGES's
# docstring for the architecture note this whole feature rests on: this
# app has no client-side router, so the code-defined PAGES catalog IS the
# route registry (the same pattern already used for PERMISSIONS/
# DEFAULT_ROLES), and repositories.sync_pages() -- not a frontend-
# triggerable endpoint, see sync_pages_endpoint below -- is the discovery
# mechanism. Every endpoint here requires pages.manage, same gate pattern
# as every other admin/* route; GET /auth/me (above) is the only place a
# non-admin user's own effective page set is exposed, read-only, no
# separate endpoint needed for that.
# ---------------------------------------------------------------------------
def _page_with_summary(page: dict, policies_by_page: dict) -> dict:
    policies = policies_by_page.get(page["page_id"], [])
    granted = sorted(p["role_key"] for p in policies if p.get("allowed"))
    return dict(page, roles_granted=granted, roles_granted_count=len(granted))


@bp.route(route="manage/pages", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_pages_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Page & Access Management table. Optional query filters:
    ?module=..., ?discovery_status=discovered|configured|stale,
    ?is_protected=true|false -- all applied client-side-equivalent here
    (small dataset, a handful of pages, no pagination needed)."""
    auth_result = require_permission(req, "pages.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        pages = repositories.list_pages()
        all_policies = repositories.list_all_page_access_policies()
    except Exception as e:
        return _cosmos_unavailable_response(e)

    policies_by_page = {}
    for p in all_policies:
        policies_by_page.setdefault(p["page_id"], []).append(p)

    module = req.params.get("module")
    discovery_status = req.params.get("discovery_status")
    is_protected = req.params.get("is_protected")

    result = []
    for page in pages:
        if module and page.get("module") != module:
            continue
        if discovery_status and page.get("discovery_status") != discovery_status:
            continue
        if is_protected is not None and str(page.get("is_protected")).lower() != is_protected.lower():
            continue
        result.append(_page_with_summary(page, policies_by_page))

    return _json_response({"count": len(result), "pages": result})


@bp.route(route="manage/pages/sync", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def sync_pages_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Reconciles the code-defined PAGES catalog against the persisted
    registry -- see repositories.sync_pages()'s docstring. Not something a
    frontend page click strictly needs to trigger for normal operation
    (the registry is also reconciled at Function App startup, same
    pattern as ensure_default_roles()), but exposed here so an
    administrator can force a re-sync (e.g. right after a deploy that
    added a page) without redeploying or restarting anything, and so this
    is independently testable."""
    auth_result = require_permission(req, "pages.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        result = repositories.sync_pages()
    except Exception as e:
        return _cosmos_unavailable_response(e)

    for pid in result["new"]:
        _audit_admin_action("page_discovered", actor, page_id=pid)
    for pid in result["updated"]:
        _audit_admin_action("page_metadata_changed", actor, page_id=pid)
    for pid in result["stale"]:
        _audit_admin_action("page_marked_stale", actor, page_id=pid)

    return _json_response(result)


@bp.route(route="manage/pages/{page_id}/access", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_page_access_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Page metadata plus the full role x allowed access matrix (every
    role in the system, not just roles with an explicit policy -- a role
    with no policy document is reported as allowed=False, reflecting the
    real deny-by-default effective state, not just "no opinion recorded")."""
    auth_result = require_permission(req, "pages.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    page_id = req.route_params.get("page_id")
    try:
        page = repositories.get_page(page_id)
        if page is None:
            return _error_response(f"Page '{page_id}' not found.", status_code=404)
        roles = repositories.list_roles()
        policies = repositories.get_page_access_policies(page_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    allowed_by_role = {p["role_key"]: p.get("allowed", False) for p in policies}
    matrix = [
        {"role_key": r["role_key"], "role_display_name": r["display_name"], "allowed": allowed_by_role.get(r["role_key"], False)}
        for r in roles
    ]
    return _json_response({"page": page, "access": matrix})


@bp.route(route="manage/pages/{page_id}/access", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def set_page_access_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Grant/revoke page access per role. Body: {"policies": [{"role_key":
    "recruiter", "allowed": true}, ...]} -- a batch of one or more
    (role_key, allowed) pairs, so the whole table on the config screen can
    be saved in one call. Each pair is validated and persisted
    independently; an unknown role_key in the batch fails the whole
    request (400) before anything is written, so a typo can't leave the
    policy set half-updated."""
    auth_result = require_permission(req, "pages.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    page_id = req.route_params.get("page_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    policies = body.get("policies")
    if not isinstance(policies, list) or not policies:
        return _error_response("'policies' must be a non-empty list of {role_key, allowed}.", status_code=400)
    for entry in policies:
        if not isinstance(entry, dict) or "role_key" not in entry or "allowed" not in entry:
            return _error_response("Each policy entry needs 'role_key' and 'allowed'.", status_code=400)
        if not isinstance(entry["allowed"], bool):
            return _error_response(f"'allowed' must be a boolean for role '{entry.get('role_key')}'.", status_code=400)

    try:
        page = repositories.get_page(page_id)
        if page is None:
            return _error_response(f"Page '{page_id}' not found.", status_code=404)
        for entry in policies:
            if repositories.get_role(entry["role_key"]) is None:
                return _error_response(f"Unknown role '{entry['role_key']}'.", status_code=400)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    try:
        for entry in policies:
            repositories.set_page_access(page_id, entry["role_key"], entry["allowed"], actor["username"])
        # A page that had zero policies (discovery_status="discovered")
        # just received its first configuration -- flip it to
        # "configured" so the Admin Panel's warning banner clears
        # immediately, without waiting for the next sync.
        if page.get("discovery_status") != "configured":
            page["discovery_status"] = "configured"
            repositories.update_page(page)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("page_access_changed", actor, page_id=page_id, policies=policies)
    return get_page_access_endpoint(req)


# ---------------------------------------------------------------------------
# AI Observability Center (/admin/ai-observability*). Requires
# observability.read, same require_permission() gate as everything else.
# Reads only local JSONL logs (logs/llm_observability.jsonl,
# logs/ai_traces.jsonl, via observability.py) -- no Cosmos DB involved, so
# this works with zero Azure resources provisioned, and the pipeline that
# WRITES these logs (observability.record_llm_call/record_ai_trace, called
# directly from llm_utils.py/embeddings.py/document_intelligence_client.py)
# runs completely independently of whether anyone ever opens this admin
# page -- per this feature's "keep the observability pipeline independent
# of the [admin] UI" requirement.
# ---------------------------------------------------------------------------
def _ai_trace_summary(records: list) -> dict:
    by_workflow = {}
    for r in records:
        stats = by_workflow.setdefault(r.get("workflow", "unknown"), {
            "total": 0, "succeeded": 0, "failed": 0, "started": 0, "total_duration_ms": 0.0,
        })
        stats["total"] += 1
        status = r.get("status")
        if status in ("succeeded", "failed", "started"):
            stats[status] += 1
        stats["total_duration_ms"] += r.get("duration_ms") or 0

    for stats in by_workflow.values():
        completed = stats["succeeded"] + stats["failed"]
        stats["avg_duration_ms"] = round(stats["total_duration_ms"] / completed, 1) if completed else None
        del stats["total_duration_ms"]

    distinct_traces = len(set(r.get("trace_id") for r in records if r.get("trace_id")))
    return {"total_spans": len(records), "distinct_traces": distinct_traces, "by_workflow": by_workflow}


# Minimum data points required before a percentile is reported -- below
# this, a percentile is statistically meaningless noise, not a real
# figure. See _percentile()'s docstring.
_MIN_SAMPLE_FOR_PERCENTILE = 5


def _percentile(values: list, p: float):
    """Nearest-rank percentile over `values` (0 < p < 100). Returns None
    if fewer than _MIN_SAMPLE_FOR_PERCENTILE data points exist -- the
    caller must show an explicit "insufficient sample" state instead of
    this, never fabricate a number from too little data (Phase 3/4
    requirement)."""
    clean = sorted(v for v in values if v is not None)
    if len(clean) < _MIN_SAMPLE_FOR_PERCENTILE:
        return None
    k = max(0, min(len(clean) - 1, int(round((p / 100.0) * (len(clean) - 1)))))
    return round(clean[k], 1)


def _latency_stats(durations: list) -> dict:
    clean = [d for d in durations if d is not None]
    avg = round(sum(clean) / len(clean), 1) if clean else None
    return {
        "sample_count": len(clean),
        "avg_ms": avg,
        "p50_ms": _percentile(clean, 50),
        "p95_ms": _percentile(clean, 95),
        "p99_ms": _percentile(clean, 99),
        "insufficient_sample": len(clean) < _MIN_SAMPLE_FOR_PERCENTILE,
    }


# The full stage list this feature's spec names, for "instrumentation
# coverage" -- which of these this repo actually has code for and
# traces, vs which don't exist in this architecture at all (see
# docs/AI_OBSERVABILITY_AZURE_HUB.md's investigation notes). Stages this
# repo genuinely doesn't have (e.g. "vector indexing" as a distinct
# service, "safety checks" as a separate pass) are listed as
# not_applicable, not silently omitted -- so "coverage" never looks
# artificially low against stages that were never real to begin with.
_DOCUMENTED_STAGES = [
    "document_extraction", "chunking", "embedding_generation", "retrieval_ranking",
    "resume_chunk_summarization", "resume_merge_structured_extraction",
    "output_parsing_validation", "persistence", "document_extraction",
]
_STAGES_NOT_APPLICABLE_TO_THIS_REPO = [
    "vector_indexing (no separate indexing service -- LocalVectorStore.add() is part of the embedding step)",
    "safety_checks (no content-safety/moderation pass exists anywhere in this repo)",
    "citation_grounding_checks (no RAG citation mechanism exists -- resume scoring doesn't cite retrieved chunks)",
]


def _instrumentation_coverage(observed_stages: set) -> dict:
    documented = sorted(set(_DOCUMENTED_STAGES))
    covered = sorted(s for s in documented if s in observed_stages)
    missing = sorted(s for s in documented if s not in observed_stages)
    return {
        "documented_stage_count": len(documented),
        "covered_stage_count": len(covered),
        "coverage_pct": round(100.0 * len(covered) / len(documented), 1) if documented else None,
        "covered_stages": covered,
        "uninstrumented_stages": missing,
        "not_applicable_to_this_repo": _STAGES_NOT_APPLICABLE_TO_THIS_REPO,
    }


def _cost_breakdown(records: list) -> dict:
    """Real cost, grouped by workflow and by model -- every figure traces
    back to a cost_source tag (langchain_openai_callback or
    documented_estimate, see observability.py/model_pricing.py), never a
    number with no documented origin. A span with no cost_usd (e.g. a
    non-LLM stage, or a model this repo has no price for) is excluded
    from the total, not treated as $0."""
    by_workflow = {}
    by_model = {}
    total_cost = 0.0
    priced_count = 0
    for r in records:
        meta = r.get("metadata") or {}
        cost = meta.get("cost_usd")
        if cost is None:
            continue
        priced_count += 1
        total_cost += cost
        wf = r.get("workflow", "unknown")
        by_workflow[wf] = round(by_workflow.get(wf, 0.0) + cost, 8)
        model = meta.get("model", "unknown")
        by_model[model] = round(by_model.get(model, 0.0) + cost, 8)
    return {
        "total_cost_usd": round(total_cost, 6),
        "priced_span_count": priced_count,
        "by_workflow": by_workflow,
        "by_model": by_model,
        "pricing_version": model_pricing.PRICING_VERSION,
        "pricing_source": model_pricing.PRICING_SOURCE,
        "pricing_last_updated": model_pricing.PRICING_LAST_UPDATED,
    }


def _group_spans_into_traces(spans: list) -> list:
    """Groups AI trace spans by trace_id into trace summaries -- there is
    no separate persisted "AITrace" document anywhere (see observability.py's
    module docstring for why); a trace is this computed view over its
    spans, always consistent with the span data by construction."""
    by_trace = {}
    for span in spans:
        tid = span.get("trace_id")
        if not tid:
            continue
        by_trace.setdefault(tid, []).append(span)

    summaries = []
    for trace_id, trace_spans in by_trace.items():
        trace_spans_sorted = sorted(trace_spans, key=lambda s: s.get("logged_at") or "")
        statuses = {s.get("status") for s in trace_spans}
        overall_status = "failed" if "failed" in statuses else ("started" if "started" in statuses else "succeeded")
        durations = [s.get("duration_ms") for s in trace_spans if s.get("duration_ms") is not None]
        costs = [(s.get("metadata") or {}).get("cost_usd") for s in trace_spans]
        costs = [c for c in costs if c is not None]
        correlation_ids = {s.get("correlation_id") for s in trace_spans if s.get("correlation_id")}
        workflows = {s.get("workflow") for s in trace_spans if s.get("workflow")}
        summaries.append({
            "trace_id": trace_id,
            "correlation_id": next(iter(correlation_ids), None),
            "workflow": next(iter(workflows), "unknown") if len(workflows) == 1 else "mixed",
            "status": overall_status,
            "span_count": len(trace_spans),
            "stages": [s.get("stage") for s in trace_spans_sorted],
            "started_at": trace_spans_sorted[0].get("logged_at") if trace_spans_sorted else None,
            "ended_at": trace_spans_sorted[-1].get("logged_at") if trace_spans_sorted else None,
            "total_duration_ms": round(sum(durations), 1) if durations else None,
            "total_cost_usd": round(sum(costs), 8) if costs else None,
            "has_error": "failed" in statuses,
        })
    return summaries


@bp.route(route="manage/ai-observability/summary", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def ai_observability_summary_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Unified view: existing per-label LLM call metrics (tokens/cost,
    reused from observability_summary_endpoint's logic, not duplicated)
    PLUS the new AI pipeline-stage trace summary (embedding generation,
    document extraction, etc. -- stages that had zero observability
    before this feature) PLUS latency percentiles, cost breakdown by
    workflow/model, and instrumentation coverage. Never fabricates data
    -- an empty/missing log file returns real zeros, and a percentile
    with too little sample data is explicitly marked unavailable rather
    than computed from noise."""
    auth_result = require_permission(req, "observability.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result

    llm_summary = _llm_observability_summary()
    trace_records = _read_jsonl(observability.TRACE_LOG_PATH)
    trace_summary = _ai_trace_summary(trace_records)
    traces = _group_spans_into_traces(trace_records)

    span_durations = [r.get("duration_ms") for r in trace_records]
    trace_durations = [t["total_duration_ms"] for t in traces]
    observed_stages = {r.get("stage") for r in trace_records if r.get("stage")}
    failed_traces = sum(1 for t in traces if t["has_error"])

    return _json_response({
        "llm_calls": llm_summary,
        "ai_traces": trace_summary,
        "requests": {
            "total": len(traces),
            "failed": failed_traces,
            "succeeded": len(traces) - failed_traces,
            "failure_rate": round(failed_traces / len(traces), 4) if traces else None,
        },
        "span_latency": _latency_stats(span_durations),
        "trace_latency": _latency_stats(trace_durations),
        "cost": _cost_breakdown(trace_records),
        "instrumentation_coverage": _instrumentation_coverage(observed_stages),
        "recent_traces": sorted(traces, key=lambda t: t.get("started_at") or "", reverse=True)[:50],
    })


@bp.route(route="manage/ai-observability/traces", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_ai_traces_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Trace Explorer list -- TRACE summaries (one row per end-to-end
    workflow run, grouped from its spans by trace_id -- see
    _group_spans_into_traces()), not raw spans. Filters: ?workflow=,
    ?status=, ?correlation_id= (substring match -- the safe identifier a
    user would actually search by, e.g. a candidate_id), ?trace_id=
    (exact). Paginated via ?limit=/?offset= (default 50/0, limit capped
    at 200) and sortable via ?sort_by=started_at|total_duration_ms and
    ?order=asc|desc (default started_at desc -- most recent first)."""
    auth_result = require_permission(req, "observability.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result

    workflow = req.params.get("workflow")
    status = req.params.get("status")
    correlation_id = req.params.get("correlation_id")
    trace_id_filter = req.params.get("trace_id")
    sort_by = req.params.get("sort_by", "started_at")
    order = req.params.get("order", "desc")
    if sort_by not in ("started_at", "total_duration_ms"):
        return _error_response("'sort_by' must be 'started_at' or 'total_duration_ms'.", status_code=400)
    if order not in ("asc", "desc"):
        return _error_response("'order' must be 'asc' or 'desc'.", status_code=400)
    try:
        limit = min(int(req.params.get("limit", 50)), 200)
        offset = max(int(req.params.get("offset", 0)), 0)
    except ValueError:
        return _error_response("'limit' and 'offset' must be integers.", status_code=400)

    trace_records = _read_jsonl(observability.TRACE_LOG_PATH)
    traces = _group_spans_into_traces(trace_records)

    if workflow:
        traces = [t for t in traces if t["workflow"] == workflow]
    if status:
        traces = [t for t in traces if t["status"] == status]
    if correlation_id:
        traces = [t for t in traces if t["correlation_id"] and correlation_id in t["correlation_id"]]
    if trace_id_filter:
        traces = [t for t in traces if t["trace_id"] == trace_id_filter]

    traces.sort(key=lambda t: (t.get(sort_by) if t.get(sort_by) is not None else ""), reverse=(order == "desc"))
    total_count = len(traces)
    page = traces[offset: offset + limit]

    return _json_response({"count": total_count, "limit": limit, "offset": offset, "traces": page})


@bp.route(route="manage/ai-observability/traces/{trace_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_ai_trace_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Full span-by-span detail for ONE trace -- requires the stronger
    observability.traces.read (trace detail includes stage-level
    metadata, closer to raw debugging information than the aggregate
    summary/list views). Spans are ordered by logged_at (execution
    order) and each carries parent_span_id for hierarchy, so the
    frontend can render a stage-by-stage timeline distinguishing
    succeeded/failed/started spans -- there is no "skipped" or
    "missing instrumentation" span representation today since every
    stage that runs is unconditionally traced (no conditional
    instrumentation gaps exist in the wired call sites yet)."""
    auth_result = require_permission(req, "observability.traces.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result

    trace_id = req.route_params.get("trace_id")
    trace_records = _read_jsonl(observability.TRACE_LOG_PATH)
    spans = sorted([r for r in trace_records if r.get("trace_id") == trace_id], key=lambda r: r.get("logged_at") or "")
    if not spans:
        return _error_response(f"No spans found for trace '{trace_id}'.", status_code=404)

    summary = _group_spans_into_traces(spans)[0]
    return _json_response({"trace": summary, "spans": spans})


# ---------------------------------------------------------------------------
# Azure Observability Report (/manage/observability-report*) -- the one
# genuinely new piece of observability work in this repo: everything above
# (ai-observability/*, azure-readiness/*) only ever reads LOCAL data
# (JSONL files, or a live az CLI/SDK call at click-time). Nothing before
# this queried Azure Monitor/Log Analytics for already-ingested telemetry.
# azure_monitor_query.py is the real KQL query layer; this section is thin
# HTTP wiring plus a short in-process TTL cache so a dashboard with several
# charts doesn't fire a fresh Log Analytics query per chart per page load.
# Deliberately reuses rather than re-implements: Azure per-service health
# is the existing azure_readiness endpoints, AI cost/token/trace detail is
# the existing ai-observability endpoints, Azure resource cost is the
# existing azure-readiness/cost-usage endpoint -- this page's own unique
# surface is real Azure-ingested telemetry (request volume, latency,
# AI usage, recent errors, ingestion health), not a re-skin of those.
# ---------------------------------------------------------------------------
import azure_monitor_query  # noqa: E402

_observability_report_cache = {}  # (query_name, hours) -> (monotonic_expiry, result)
_OBSERVABILITY_REPORT_CACHE_TTL_SECONDS = 60


def _cached_query(query_name: str, hours: float, fn):
    import time as _time
    key = (query_name, hours)
    cached = _observability_report_cache.get(key)
    now = _time.monotonic()
    if cached and cached[0] > now:
        return cached[1]
    result = fn()
    _observability_report_cache[key] = (now + _OBSERVABILITY_REPORT_CACHE_TTL_SECONDS, result)
    return result


def _parse_hours_param(req: func.HttpRequest, default: float = 24.0, maximum: float = 168.0):
    raw = req.params.get("hours")
    if raw is None:
        return default, None
    try:
        hours = float(raw)
    except ValueError:
        return None, "'hours' must be a number."
    if hours <= 0 or hours > maximum:
        return None, f"'hours' must be between 0 (exclusive) and {maximum}."
    return hours, None


@bp.route(route="manage/observability-report/summary", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def observability_report_summary_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Executive KPI rollup for the Azure Observability Report page.
    Composes: real Azure Monitor ingestion health + request/latency KPIs
    (azure_monitor_query.py, cached), plus the existing local LLM/trace
    summary (reused, not recomputed) -- so a reader can see both "what the
    app itself recorded" and "what Azure Monitor actually ingested" as two
    explicitly separate, separately-labeled views, never merged into one
    number. `overall_status` is derived from real ingestion + connectivity
    evidence only -- it is never set to a healthy-sounding value just
    because this endpoint itself returned 200."""
    auth_result = require_permission(req, "observability.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    hours, err = _parse_hours_param(req)
    if err:
        return _error_response(err, status_code=400)

    telemetry_health = _cached_query("telemetry_health", 0, azure_monitor_query.get_telemetry_health)
    request_volume = _cached_query("request_volume", hours, lambda: azure_monitor_query.get_request_volume(hours=hours))
    latency = _cached_query("latency", hours, lambda: azure_monitor_query.get_latency_percentiles(hours=hours))
    ai_usage = _cached_query("ai_usage", hours, lambda: azure_monitor_query.get_ai_usage(hours=hours))

    local_llm_summary = _llm_observability_summary()
    trace_records = _read_jsonl(observability.TRACE_LOG_PATH)
    local_trace_summary = _ai_trace_summary(trace_records)

    total_requests = sum(b["total"] for b in request_volume.get("buckets", [])) if request_volume["available"] else None
    succeeded = sum(b["succeeded"] for b in request_volume.get("buckets", [])) if request_volume["available"] else None
    failed = sum(b["failed"] for b in request_volume.get("buckets", [])) if request_volume["available"] else None

    if not azure_monitor_query.is_configured():
        overall_status = "NOT_CONFIGURED"
    elif not telemetry_health.get("azure_configured"):
        overall_status = "UNKNOWN"
    elif not telemetry_health.get("ingestion_confirmed"):
        overall_status = "CONFIGURED_NO_INGESTION"
    elif failed and total_requests and failed / total_requests > 0.1:
        overall_status = "DEGRADED"
    else:
        overall_status = "HEALTHY"

    return _json_response({
        "reporting_period_hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall_status,
        "telemetry_health": telemetry_health,
        "azure_telemetry": {
            "configured": azure_monitor_query.is_configured(),
            "total_requests": total_requests,
            "succeeded_requests": succeeded,
            "failed_requests": failed,
            "success_rate": round(succeeded / total_requests, 4) if (total_requests and succeeded is not None) else None,
            "failure_rate": round(failed / total_requests, 4) if (total_requests and failed is not None) else None,
            "latency": latency,
            "ai_requests": sum(m["calls"] for m in ai_usage.get("by_model", [])) if ai_usage["available"] else None,
            "ai_total_tokens": sum(m["total_tokens"] or 0 for m in ai_usage.get("by_model", [])) if ai_usage["available"] else None,
            "ai_total_cost_usd": round(sum(m["total_cost_usd"] or 0 for m in ai_usage.get("by_model", [])), 6) if ai_usage["available"] else None,
            "unavailable_reason": None if request_volume["available"] else request_volume["reason"],
        },
        "local_observability": {
            "llm_calls": local_llm_summary,
            "ai_traces": local_trace_summary,
            "note": "Sourced from this process's local JSONL logs -- always available regardless of Azure Monitor configuration, not the same data source as azure_telemetry above.",
        },
    })


@bp.route(route="manage/observability-report/telemetry", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def observability_report_telemetry_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Chart data for the report page: real Azure Monitor request-volume
    buckets, AI usage by model, and recent errors, all over ?hours=
    (default 24, max 168). Each section is independently
    available/unavailable -- a failure in one (e.g. a malformed query)
    never blanks out the others."""
    auth_result = require_permission(req, "observability.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    hours, err = _parse_hours_param(req)
    if err:
        return _error_response(err, status_code=400)
    limit = req.params.get("error_limit", "20")
    try:
        error_limit = min(max(int(limit), 1), 100)
    except ValueError:
        return _error_response("'error_limit' must be an integer.", status_code=400)

    return _json_response({
        "reporting_period_hours": hours,
        "request_volume": _cached_query("request_volume", hours, lambda: azure_monitor_query.get_request_volume(hours=hours)),
        "ai_usage": _cached_query("ai_usage", hours, lambda: azure_monitor_query.get_ai_usage(hours=hours)),
        "recent_errors": _cached_query(f"recent_errors_{error_limit}", hours,
                                         lambda: azure_monitor_query.get_recent_errors(hours=hours, limit=error_limit)),
    })


@bp.route(route="manage/observability-report/export", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def observability_report_export_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """CSV export of the request-volume time series -- same permission as
    the rest of this report (observability.read); no additional data is
    exposed beyond what the page itself already shows. Never includes
    secrets, connection strings, or candidate PII -- this endpoint's data
    is infra/AI-operations telemetry only."""
    auth_result = require_permission(req, "observability.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    hours, err = _parse_hours_param(req)
    if err:
        return _error_response(err, status_code=400)

    result = _cached_query("request_volume", hours, lambda: azure_monitor_query.get_request_volume(hours=hours))
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "total", "succeeded", "failed"])
    if result["available"]:
        for b in result["buckets"]:
            writer.writerow([b["timestamp"], b["total"], b["succeeded"], b["failed"]])
    return func.HttpResponse(
        buf.getvalue(),
        status_code=200 if result["available"] else 503,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=observability_request_volume.csv"},
    )


# ---------------------------------------------------------------------------
# Azure Service Hub (/admin/azure-services*) -- an adapter registry
# (azure_integrations.ADAPTER_TYPES) plus persisted, administrator-
# registered INSTANCES of those adapters (repositories.py's
# integration_instances/integration_health_checks). Three distinct
# permission-gated actions, not one: viewing the inventory (azure.read),
# viewing an instance's detailed config/diagnostics (azure.config.read),
# and the write actions (azure.register/azure.config.write/azure.enable/
# azure.manage/azure.resources.discover) -- least privilege, not one
# blanket "admin" gate. Removing a registration (DELETE) only ever
# deletes the CDPI-side record -- see that endpoint's docstring for why
# it can never touch the real Azure resource.
# ---------------------------------------------------------------------------
_azure_check_last_run = {}  # (actor_id, integration_id) -> monotonic seconds, per-process only
_AZURE_CHECK_MIN_INTERVAL_SECONDS = 10


def _rate_limited(key, min_interval_seconds, store=None) -> bool:
    """Returns True if `key` was already used within the last
    min_interval_seconds (i.e. the caller should be refused), False and
    records `key` as used-now otherwise. In-memory, per Functions-host
    process -- a real limitation under multi-instance scale-out (each
    instance has its own counter), documented rather than solved with a
    distributed store this feature doesn't need yet. `store` defaults to
    _azure_check_last_run (this helper's original caller) -- pass a
    DIFFERENT dict for an unrelated rate limit (e.g. _rag_preview_last_run)
    so the two never share state and one feature's calls can't trip the
    other's limiter."""
    import time as _time

    if store is None:
        store = _azure_check_last_run
    now = _time.monotonic()
    last = store.get(key)
    if last is not None and (now - last) < min_interval_seconds:
        return True
    store[key] = now
    return False


@bp.route(route="manage/azure-service-adapters", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_azure_adapters_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """The adapter TYPE registry -- what CDPI could register an instance
    of, independent of what's actually registered today. Powers the "Add
    Integration" wizard's service-type picker.

    Deliberately NOT nested under manage/azure-services/* (it was
    manage/azure-services/adapters until this fix): a real, reproduced bug
    found live in the browser -- Azure Functions Core Tools' local Python
    v2 HTTP routing does not reliably prefer this literal route over the
    sibling manage/azure-services/{integration_id} route, so a request to
    .../adapters was being matched to the dynamic route with
    integration_id="adapters" and 404ing with "Integration 'adapters' not
    found." Moving this one static segment out from under the
    parameterized route's prefix sidesteps the ambiguity entirely rather
    than depending on the router's (here, unreliable) specificity
    ordering."""
    auth_result = require_permission(req, "azure.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    return _json_response({"count": len(azure_integrations.ADAPTER_TYPES), "adapters": list(azure_integrations.ADAPTER_TYPES.values())})


@bp.route(route="manage/azure-services", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_azure_integrations_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Registered integration INSTANCES with their CACHED status (the
    result of their most recent health check, if any) -- no network
    calls here, so this is always fast; use POST .../check for an actual
    live connectivity check, which updates that cache."""
    auth_result = require_permission(req, "azure.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        instances = repositories.list_integration_instances()
    except Exception as e:
        return _cosmos_unavailable_response(e)

    result = []
    for instance in instances:
        adapter = azure_integrations.get_adapter(instance["adapter_type"]) or {}
        presence = azure_integrations.secret_presence(instance["adapter_type"])
        result.append({
            "integration_id": instance["id"], "adapter_type": instance["adapter_type"],
            "display_name": instance["display_name"], "environment": instance["environment"],
            "category": adapter.get("category"), "enabled": instance["enabled"],
            "configured": all(presence.values()) if presence else False,
            "last_connection_outcome": instance.get("last_connection_outcome"),
            "last_health_checked_at": instance.get("last_health_checked_at"),
            "resource_discovery_supported": adapter.get("capabilities", {}).get("resource_discovery", False),
        })
    return _json_response({"count": len(result), "integrations": result})


@bp.route(route="manage/azure-services/validate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def validate_azure_integration_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Validates a prospective adapter_type's configuration (secret
    presence) BEFORE registering -- step 7-9 of the spec's Add
    Integration workflow (validate, then test connectivity, then review
    the result, before registration)."""
    auth_result = require_permission(req, "azure.register")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    adapter_type = body.get("adapter_type")
    if not azure_integrations.get_adapter(adapter_type):
        return _error_response(f"Unknown adapter type '{adapter_type}'.", status_code=400)

    errors = azure_integrations.validate_config(adapter_type)
    return _json_response({"adapter_type": adapter_type, "valid": not errors, "errors": errors})


@bp.route(route="manage/azure-services", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def register_azure_integration_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Registers a new integration instance. Body: {"integration_id": "...",
    "adapter_type": "...", "display_name": "...", "environment": "...",
    "config": {...} (optional, non-secret fields only -- e.g.
    subscription_id/resource_group for resource discovery; see
    azure_integrations.py's module docstring for why secret VALUES are
    never accepted here)}.

    Duplicate integration_id -> 409. A new registration is ALWAYS created
    disabled -- "enable it only after required validation succeeds" is
    enforced by the separate PUT .../enabled endpoint refusing to enable
    an instance that has never recorded a successful health check."""
    auth_result = require_permission(req, "azure.register")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    integration_id = (body.get("integration_id") or "").strip()
    adapter_type = body.get("adapter_type")
    display_name = (body.get("display_name") or "").strip()
    environment = (body.get("environment") or "").strip()
    config = body.get("config") or {}

    if not integration_id or not display_name or not environment:
        return _error_response("'integration_id', 'display_name', and 'environment' are required.", status_code=400)
    if not azure_integrations.get_adapter(adapter_type):
        return _error_response(f"Unknown adapter type '{adapter_type}'.", status_code=400)
    if azure_integrations.is_planned(adapter_type):
        return _error_response(
            f"'{azure_integrations.get_adapter(adapter_type)['name']}' is planned but not implemented in this "
            "repository -- there is no code path to register or configure it yet.", status_code=400,
        )
    if not isinstance(config, dict):
        return _error_response("'config' must be an object.", status_code=400)
    secret_keys_in_config = [f["key"] for f in azure_integrations.get_adapter(adapter_type)["config_schema"] if f["secret"] and f["key"] in config]
    if secret_keys_in_config:
        return _error_response(
            f"Secret field(s) {secret_keys_in_config} cannot be submitted through this API -- configure them via "
            "your .env file or Key Vault (see secrets_loader.py).", status_code=400,
        )

    try:
        if repositories.get_integration_instance(integration_id) is not None:
            return _error_response(f"Integration '{integration_id}' is already registered.", status_code=409)
        instance = repositories.create_integration_instance(integration_id, adapter_type, display_name, environment, config, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("integration_registered", actor, integration_id=integration_id, adapter_type=adapter_type)
    return _json_response(instance, status_code=201)


@bp.route(route="manage/azure-services/{integration_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_azure_integration_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Full detail: overview (instance + adapter metadata), configuration
    (non-secret fields + secret PRESENCE only, never values), and recent
    diagnostics (last 10 health checks) -- requires the stronger
    azure.config.read, not just azure.read."""
    auth_result = require_permission(req, "azure.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    integration_id = req.route_params.get("integration_id")
    try:
        instance = repositories.get_integration_instance(integration_id)
        if instance is None:
            return _error_response(f"Integration '{integration_id}' not found.", status_code=404)
        history = repositories.list_integration_health_checks(integration_id, limit=10)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    adapter = azure_integrations.get_adapter(instance["adapter_type"]) or {}
    presence = azure_integrations.secret_presence(instance["adapter_type"])
    return _json_response({
        "instance": instance, "adapter": adapter, "secrets_configured": presence,
        "recent_health_checks": history,
    })


@bp.route(route="manage/azure-services/{integration_id}", methods=["DELETE"], auth_level=func.AuthLevel.FUNCTION)
def remove_azure_integration_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Removes the CDPI-side REGISTRATION only -- this can NEVER delete,
    stop, or otherwise touch the real Azure resource (there is no
    resource-delete operation implemented anywhere in this module, see
    azure_integrations.py's docstring). Three distinct concepts the spec
    requires never be conflated: (1) this endpoint, removing CDPI's
    record of the integration; (2) PUT .../enabled, disabling CDPI's USE
    of it without forgetting it; (3) actually deleting the Azure
    resource, which this codebase has no code path for at all."""
    auth_result = require_permission(req, "azure.register")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    integration_id = req.route_params.get("integration_id")
    try:
        instance = repositories.get_integration_instance(integration_id)
        if instance is None:
            return _error_response(f"Integration '{integration_id}' not found.", status_code=404)
        repositories.get_container("integration_instances").delete_item(item=integration_id, partition_key=integration_id)
        repositories.record_integration_config_change(integration_id, "registration_removed", actor["username"], {"adapter_type": instance["adapter_type"]})
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action(
        "integration_registration_removed", actor, integration_id=integration_id,
        note="Removed CDPI's registration only -- the underlying Azure resource, if any, was not touched.",
    )
    return _json_response({"integration_id": integration_id, "status": "registration_removed"})


@bp.route(route="manage/azure-services/{integration_id}/config", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def update_azure_integration_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Updates an instance's non-secret config. Body: {"config": {...},
    "expected_version": N}. `expected_version` must match the instance's
    CURRENT config_version or this returns 409 -- optimistic concurrency,
    so a stale edit from a second admin tab can't silently clobber a
    more recent change (the spec's explicit "do not overwrite existing
    working configuration without an explicit administrative action,"
    enforced structurally, not just by convention)."""
    auth_result = require_permission(req, "azure.config.write")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    integration_id = req.route_params.get("integration_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    config = body.get("config")
    expected_version = body.get("expected_version")
    if not isinstance(config, dict):
        return _error_response("'config' must be an object.", status_code=400)
    if expected_version is None:
        return _error_response("'expected_version' is required (optimistic concurrency -- read the current instance first).", status_code=400)

    try:
        instance = repositories.get_integration_instance(integration_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if instance is None:
        return _error_response(f"Integration '{integration_id}' not found.", status_code=404)
    if instance["config_version"] != expected_version:
        return _error_response(
            f"Configuration has changed since you last read it (current version {instance['config_version']}, "
            f"expected {expected_version}) -- reload and reapply your change.", status_code=409,
        )

    secret_keys_in_config = [f["key"] for f in azure_integrations.get_adapter(instance["adapter_type"])["config_schema"] if f["secret"] and f["key"] in config]
    if secret_keys_in_config:
        return _error_response(f"Secret field(s) {secret_keys_in_config} cannot be submitted through this API.", status_code=400)

    instance["config"] = config
    instance["config_version"] = expected_version + 1
    instance["updated_by"] = actor["username"]
    # A config change invalidates any prior "healthy" verdict -- the last check
    # was against the OLD config, so it can't vouch for the new one. Force a
    # fresh connection check before this can be (re-)enabled -- the spec's
    # "revalidate restored/changed configuration before activation," enforced
    # structurally via the existing enable-requires-healthy-check gate.
    instance["last_connection_outcome"] = None
    instance["last_health_checked_at"] = None
    instance["last_failure"] = None
    if instance.get("enabled"):
        instance["enabled"] = False
        instance["lifecycle_status"] = "draft"
    try:
        updated = repositories.update_integration_instance(instance)
        repositories.record_integration_config_change(
            integration_id, "config_updated", actor["username"],
            {"config": config, "config_version": instance["config_version"]},
        )
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("integration_config_updated", actor, integration_id=integration_id, new_config_version=instance["config_version"])
    return _json_response(updated)


@bp.route(route="manage/azure-services/{integration_id}/enabled", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def set_azure_integration_enabled_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Enables/disables CDPI's USE of a registered integration -- does
    NOT remove its registration (see DELETE .../{integration_id}) and
    does NOT touch the real Azure resource either way. Body:
    {"enabled": true|false}. Enabling requires the instance's last
    recorded health check to have outcome "healthy" -- "enable only
    after required validation succeeds," enforced here, not left to the
    frontend to remember to check first."""
    auth_result = require_permission(req, "azure.enable")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    integration_id = req.route_params.get("integration_id")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)

    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return _error_response("'enabled' must be a boolean.", status_code=400)

    try:
        instance = repositories.get_integration_instance(integration_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if instance is None:
        return _error_response(f"Integration '{integration_id}' not found.", status_code=404)

    if enabled and instance.get("last_connection_outcome") != "healthy":
        return _error_response(
            "Can't enable this integration -- it has no recorded successful ('healthy') connection check yet. "
            "Run POST .../check first.", status_code=409,
        )

    instance["enabled"] = enabled
    instance["lifecycle_status"] = "active" if enabled else "disabled"
    instance["updated_by"] = actor["username"]
    try:
        updated = repositories.update_integration_instance(instance)
        repositories.record_integration_config_change(
            integration_id, "activated" if enabled else "deactivated", actor["username"],
            {"config_version": instance["config_version"]},
        )
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("integration_enabled_changed", actor, integration_id=integration_id, enabled=enabled)
    return _json_response(updated)


@bp.route(route="manage/azure-services/{integration_id}/check", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def check_azure_integration_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Runs a real connection check (see azure_integrations.check_connection's
    docstring for exactly what "real" means per adapter type), persists
    it to health-check HISTORY (not just overwriting the last result),
    updates the instance's cached status, and audit-logs the outcome.
    Rate-limited per (actor, integration) -- see _rate_limited() above."""
    auth_result = require_permission(req, "azure.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    integration_id = req.route_params.get("integration_id")

    try:
        instance = repositories.get_integration_instance(integration_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if instance is None:
        return _error_response(f"Integration '{integration_id}' not found.", status_code=404)

    if _rate_limited((actor["id"], integration_id), _AZURE_CHECK_MIN_INTERVAL_SECONDS):
        return _error_response(
            f"Checked too recently -- wait at least {_AZURE_CHECK_MIN_INTERVAL_SECONDS}s between checks of the same integration.",
            status_code=429,
        )

    # Correlated with the AI Observability Center (reusing its existing
    # trace/span telemetry, not a second logging system) -- metadata is
    # populated with the real outcome AFTER the check runs, by mutating
    # the same dict trace_stage() already holds a reference to, so this
    # is one write, not two (same pattern as embeddings.py's cost
    # tracking). The span itself "succeeds" whenever the check RAN to
    # completion -- whether the connection it found was healthy is a
    # property of the metadata, not the span status, so an "unhealthy"
    # outcome is never misreported as a failed trace.
    trace_metadata = {"integration_id": integration_id, "adapter_type": instance["adapter_type"], "config_version": instance.get("config_version")}
    with observability.trace_stage("azure_config", "connection_check", metadata=trace_metadata, correlation_id=integration_id):
        result = azure_integrations.check_connection(instance["adapter_type"])
        result["integration_id"] = integration_id
        trace_metadata["outcome"] = result["outcome"]
        trace_metadata["error_category"] = result.get("error_category")
    try:
        saved = repositories.record_integration_health_check(integration_id, result)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("integration_connection_checked", actor, integration_id=integration_id, outcome=result["outcome"])
    return _json_response(saved)


@bp.route(route="manage/azure-services/{integration_id}/health-history", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_azure_integration_health_history_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Paginated (newest-first, ?limit= default 50 max 200) health-check
    history for one integration -- the diagnostics tab's "connection-test
    history" requirement."""
    auth_result = require_permission(req, "azure.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    integration_id = req.route_params.get("integration_id")
    try:
        limit = min(int(req.params.get("limit", 50)), 200)
    except ValueError:
        return _error_response("'limit' must be an integer.", status_code=400)

    try:
        if repositories.get_integration_instance(integration_id) is None:
            return _error_response(f"Integration '{integration_id}' not found.", status_code=404)
        history = repositories.list_integration_health_checks(integration_id, limit=limit)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(history), "checks": history})


@bp.route(route="manage/azure-services/{integration_id}/discover-resources", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def discover_azure_resources_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Real Azure Resource Manager discovery, scoped to the instance's
    stored subscription_id/resource_group config -- see
    azure_integrations.discover_resources()'s docstring for exactly when
    this is supported vs. returns a clear "not supported"/"missing scope"
    error rather than a fabricated or misleadingly-empty list."""
    auth_result = require_permission(req, "azure.resources.discover")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    integration_id = req.route_params.get("integration_id")
    try:
        instance = repositories.get_integration_instance(integration_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if instance is None:
        return _error_response(f"Integration '{integration_id}' not found.", status_code=404)

    config = instance.get("config") or {}
    trace_metadata = {"integration_id": integration_id, "adapter_type": instance["adapter_type"]}
    with observability.trace_stage("azure_config", "resource_discovery", metadata=trace_metadata, correlation_id=integration_id):
        result = azure_integrations.discover_resources(
            instance["adapter_type"], config.get("subscription_id"), config.get("resource_group"),
        )
        trace_metadata["supported"] = result["supported"]
        trace_metadata["resource_count"] = len(result["resources"])
    _audit_admin_action(
        "integration_resources_discovered", actor, integration_id=integration_id,
        resource_count=len(result["resources"]), supported=result["supported"],
    )
    return _json_response(result)


@bp.route(route="manage/azure-services/{integration_id}/config-history", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_azure_integration_config_history_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Paginated (newest-first) CONFIGURATION change history -- distinct
    from health-check history (GET .../health-history): this is "what
    was changed, by whom, when" (register/config_updated/activated/
    deactivated/registration_removed/rolled_back), not "did a live
    connection succeed." Entries' safe_summary never contains a secret
    value -- register/config_updated entries include the full `config`
    dict, which is already secret-free by construction (the register/
    update endpoints reject any secret field in that payload)."""
    auth_result = require_permission(req, "azure.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    integration_id = req.route_params.get("integration_id")
    try:
        limit = min(int(req.params.get("limit", 50)), 200)
    except ValueError:
        return _error_response("'limit' must be an integer.", status_code=400)

    try:
        if repositories.get_integration_instance(integration_id) is None:
            return _error_response(f"Integration '{integration_id}' not found.", status_code=404)
        history = repositories.list_integration_config_history(integration_id, limit=limit)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(history), "history": history})


@bp.route(route="manage/azure-services/{integration_id}/config-history/{history_id}/rollback", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def rollback_azure_integration_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Restores a prior configuration snapshot from history as a NEW
    config_version (history itself is never mutated or deleted -- a
    rollback is just another forward-moving change, auditable the same
    way). Only entries with operation "config_updated" or "registered"
    carry a restorable `config` snapshot. Same as any other config
    change, this forces revalidation: last_connection_outcome is reset
    and the integration is disabled if it was enabled -- the spec's
    explicit "revalidate restored configuration before activation,"
    never assuming an old config is still safe to trust."""
    auth_result = require_permission(req, "azure.config.write")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    integration_id = req.route_params.get("integration_id")
    history_id = req.route_params.get("history_id")

    try:
        instance = repositories.get_integration_instance(integration_id)
        if instance is None:
            return _error_response(f"Integration '{integration_id}' not found.", status_code=404)
        history = repositories.list_integration_config_history(integration_id, limit=200)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    entry = next((h for h in history if h["id"] == history_id), None)
    if entry is None:
        return _error_response(f"Config history entry '{history_id}' not found for this integration.", status_code=404)
    if entry["operation"] not in ("registered", "config_updated") or "config" not in entry["safe_summary"]:
        return _error_response("This history entry has no configuration snapshot to roll back to.", status_code=400)

    instance["config"] = entry["safe_summary"]["config"]
    instance["config_version"] += 1
    instance["last_connection_outcome"] = None
    instance["last_health_checked_at"] = None
    instance["last_failure"] = None
    if instance.get("enabled"):
        instance["enabled"] = False
        instance["lifecycle_status"] = "draft"
    instance["updated_by"] = actor["username"]
    try:
        updated = repositories.update_integration_instance(instance)
        repositories.record_integration_config_change(
            integration_id, "rolled_back", actor["username"],
            {"rolled_back_to_history_id": history_id, "config": instance["config"], "config_version": instance["config_version"]},
        )
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("integration_config_rolled_back", actor, integration_id=integration_id, rolled_back_to_history_id=history_id, new_config_version=instance["config_version"])
    return _json_response(updated)


# ---------------------------------------------------------------------------
# Azure Integration Readiness (/admin/azure-readiness). A composed VIEW
# over the real data the Azure Service Hub already produces --
# azure_integrations.validate_config()/check_connection() and
# az_cli.get_account_status() -- classified into the one-status-per-
# service vocabulary azure_readiness.py documents (NOT_CONFIGURED,
# AUTHENTICATION_REQUIRED, READY, etc.). This endpoint set never
# duplicates a check: GET reads the SAME cached last_connection_outcome
# the Azure Service Hub list endpoint already shows; POST .../validate
# runs the exact same azure_integrations.check_connection() the existing
# .../check endpoint runs (same rate-limit store, so hammering either
# endpoint name for the same integration is throttled together).
# Running a readiness scan NEVER creates an Azure resource -- see the
# separate ephemeral-tests endpoints below for the only workflow in this
# app that provisions real infrastructure, and even that requires
# explicit human approval at each step.
# ---------------------------------------------------------------------------
def _check_result_from_instance(instance: dict):
    """Reconstructs the last azure_integrations.check_connection()-shaped
    result from an instance's cached last_connection_outcome/last_failure
    fields (repositories.record_integration_health_check()) -- so GET
    doesn't need to re-run a check or re-read full history just to
    classify readiness. None if no check has ever been recorded."""
    outcome = instance.get("last_connection_outcome")
    if outcome is None:
        return None
    if outcome == "healthy":
        return {"outcome": "healthy", "error_category": None, "detail": "Live connection check succeeded.", "checked_at": instance.get("last_health_checked_at")}
    failure = instance.get("last_failure") or {}
    return {
        "outcome": outcome, "error_category": failure.get("error_category"),
        "detail": failure.get("detail"), "checked_at": instance.get("last_health_checked_at"),
    }


def _readiness_record(instance: dict, az_account_status: dict) -> dict:
    return azure_readiness.readiness_for_instance(
        instance, latest_check=_check_result_from_instance(instance), az_account_status=az_account_status,
    )


@bp.route(route="manage/azure-readiness", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def azure_readiness_list_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Every registered integration's readiness, computed from already-
    cached data -- does not run a live check (see POST .../validate for
    that). Includes the az CLI account status once at the top level
    (not a service, but the shared auth context several integrations'
    readiness depends on) and also the 'not yet registered' adapters
    this repo has code for but no admin has registered an instance of,
    so NOT_INTEGRATED/NOT_CONFIGURED services aren't silently omitted."""
    auth_result = require_permission(req, "azure.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        instances = repositories.list_integration_instances()
    except Exception as e:
        return _cosmos_unavailable_response(e)

    az_status = az_cli.get_account_status()
    services = [_readiness_record(i, az_status) for i in instances]

    registered_types = {i["adapter_type"] for i in instances}
    for adapter_type, adapter in azure_integrations.ADAPTER_TYPES.items():
        if adapter_type in registered_types:
            continue
        verdict = azure_readiness.compute_readiness(adapter_type, check_result=None, az_authenticated=az_status.get("authenticated"))
        services.append({
            "integration_id": None, "adapter_type": adapter_type, "service_name": adapter["name"],
            "status": verdict["status"], "status_definition": azure_readiness.STATUS_DEFINITIONS[verdict["status"]],
            "reason": verdict["reason"] if verdict["status"] != "UNKNOWN" else "No instance of this integration is registered yet.",
            "environment": None, "display_name": None, "enabled": False, "missing_configuration": [],
            "last_validated_at": None, "last_successful_validation_at": None, "resource_identifiers": {},
            "validated_against": "config_only",
            "recommended_next_action": "Register an instance from the Azure Service Hub console." if not azure_integrations.is_planned(adapter_type) else azure_readiness.recommend_next_action(verdict["status"], adapter_type),
        })

    return _json_response({
        "count": len(services), "services": services,
        "azure_cli": az_status,
    })


@bp.route(route="manage/azure-readiness/cli-status", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def azure_readiness_cli_status_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Real Azure CLI installation + `az login` session detection (see
    az_cli.py) -- never a stored/assumed value, re-runs 'az account show'
    on every call. Read-only -- never attempts to run 'az login' itself."""
    auth_result = require_permission(req, "azure.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    return _json_response({
        "cli": az_cli.check_az_installed(),
        "account": az_cli.get_account_status(),
        "subscriptions": az_cli.list_subscriptions(),
    })


@bp.route(route="manage/azure-readiness/cost-usage", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def azure_readiness_cost_usage_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Real resource list + real month-to-date cost for AZURE_RESOURCE_GROUP
    (see az_cli.py's list_resource_group_resources/get_cost_summary) --
    never a fabricated total. If AZURE_RESOURCE_GROUP isn't configured, or
    `az` isn't authenticated, returns an honest "unavailable" shape rather
    than a 500 or a placeholder number."""
    auth_result = require_permission(req, "azure.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    resource_group = secrets_loader.get_secret("AZURE_RESOURCE_GROUP")
    if not resource_group:
        return _json_response({
            "configured": False,
            "resources": {"resources": [], "detail": "AZURE_RESOURCE_GROUP is not set."},
            "cost": {"available": False, "currency": None, "total": None, "by_resource": [],
                      "detail": "AZURE_RESOURCE_GROUP is not set."},
        })
    return _json_response({
        "configured": True,
        "resource_group": resource_group,
        "resources": az_cli.list_resource_group_resources(resource_group),
        "cost": az_cli.get_cost_summary(resource_group),
    })


@bp.route(route="manage/azure-readiness/{integration_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def azure_readiness_detail_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "azure.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    integration_id = req.route_params.get("integration_id")
    try:
        instance = repositories.get_integration_instance(integration_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if instance is None:
        return _error_response(f"Integration '{integration_id}' not found.", status_code=404)
    return _json_response(_readiness_record(instance, az_cli.get_account_status()))


@bp.route(route="manage/azure-readiness/{integration_id}/validate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def azure_readiness_validate_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Runs the SAME real check as POST .../azure-services/{id}/check
    (azure_integrations.check_connection -- never a second, parallel
    verification mechanism), persists it to the same health-check
    history, and returns the resulting readiness classification. Shares
    that endpoint's rate-limit store/interval on purpose."""
    auth_result = require_permission(req, "azure.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    integration_id = req.route_params.get("integration_id")

    try:
        instance = repositories.get_integration_instance(integration_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if instance is None:
        return _error_response(f"Integration '{integration_id}' not found.", status_code=404)

    if _rate_limited((actor["id"], integration_id), _AZURE_CHECK_MIN_INTERVAL_SECONDS):
        return _error_response(
            f"Checked too recently -- wait at least {_AZURE_CHECK_MIN_INTERVAL_SECONDS}s between checks of the same integration.",
            status_code=429,
        )

    result = azure_integrations.check_connection(instance["adapter_type"])
    result["integration_id"] = integration_id
    try:
        repositories.record_integration_health_check(integration_id, result)
        instance = repositories.get_integration_instance(integration_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("azure_readiness_validated", actor, integration_id=integration_id, outcome=result["outcome"])
    return _json_response(_readiness_record(instance, az_cli.get_account_status()))


@bp.route(route="manage/azure-readiness/validate-all", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def azure_readiness_validate_all_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Runs a real check for every registered, non-planned instance.
    An individual instance still rate-limited from a recent check is
    skipped (not errored) -- its result reflects the cached state, same
    data GET .../azure-readiness already returns for it."""
    auth_result = require_permission(req, "azure.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        instances = repositories.list_integration_instances()
    except Exception as e:
        return _cosmos_unavailable_response(e)

    az_status = az_cli.get_account_status()
    results = []
    for instance in instances:
        if azure_integrations.is_planned(instance["adapter_type"]):
            results.append(_readiness_record(instance, az_status))
            continue
        if _rate_limited((actor["id"], instance["id"]), _AZURE_CHECK_MIN_INTERVAL_SECONDS):
            results.append(dict(_readiness_record(instance, az_status), skipped_rate_limited=True))
            continue
        result = azure_integrations.check_connection(instance["adapter_type"])
        result["integration_id"] = instance["id"]
        try:
            repositories.record_integration_health_check(instance["id"], result)
            instance = repositories.get_integration_instance(instance["id"])
        except Exception as e:
            return _cosmos_unavailable_response(e)
        results.append(_readiness_record(instance, az_status))

    _audit_admin_action("azure_readiness_validated_all", actor, count=len(results))
    return _json_response({"count": len(results), "services": results})


# ---------------------------------------------------------------------------
# Ephemeral Azure Testing (Mode C: provision -> test -> destroy). The ONLY
# endpoints in this app that can create or delete real Azure
# infrastructure -- see ephemeral_test_runner.py's module docstring for
# the full safety model (dedicated resource group per run, explicit
# approval gate, ownership-tag-checked cleanup, never auto-run as part of
# an ordinary readiness scan). Gated by azure.manage (the same permission
# that already gates "run a live connection check" -- provisioning a
# throwaway test resource is the same trust tier).
# ---------------------------------------------------------------------------
@bp.route(route="manage/azure-readiness/application-insights/test-telemetry", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def application_insights_test_telemetry_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Explicit, admin-triggered real test operation
    (observability.run_connectivity_test()) -- emits one real span
    through the app's own normal telemetry path and reports a
    correlation_id an admin can search for in Application Insights to
    verify actual ingestion (this process can confirm the span was
    handed to the exporter, never that Azure finished ingesting it --
    see that function's docstring)."""
    auth_result = require_permission(req, "observability.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    result = observability.run_connectivity_test()
    _audit_admin_action("application_insights_test_telemetry", actor, correlation_id=result["correlation_id"], appinsights_configured=result["application_insights_configured"])
    return _json_response(result)


@bp.route(route="manage/azure-readiness/azure-ai-foundry/test-request", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def azure_ai_foundry_test_request_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Explicit, admin-triggered real verification of Azure OpenAI chat +
    embedding requests (llm_utils.run_azure_openai_test_request()) --
    NEVER part of any automatic readiness scan, since it is a real
    billable Azure call. Gated by azure.manage, same trust tier as
    running a live connection check or provisioning an ephemeral test
    resource."""
    auth_result = require_permission(req, "azure.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result

    with observability.trace_stage("azure_ai_foundry", "test_request", metadata={}, correlation_id=None):
        result = llm_utils.run_azure_openai_test_request()

    _audit_admin_action(
        "azure_ai_foundry_test_request", actor, configured=result["configured"],
        chat_outcome=(result["chat"] or {}).get("outcome"), embedding_outcome=(result["embedding"] or {}).get("outcome"),
    )
    return _json_response(result)


@bp.route(route="manage/azure-readiness/ephemeral-tests", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_ephemeral_test_runs_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "azure.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        limit = min(int(req.params.get("limit", 50)), 200)
    except ValueError:
        return _error_response("'limit' must be an integer.", status_code=400)
    try:
        runs = repositories.list_ephemeral_test_runs(limit=limit)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({
        "count": len(runs), "runs": runs,
        "allowed_resource_plans": ephemeral_test_runner.ALLOWED_RESOURCE_PLANS,
    })


@bp.route(route="manage/azure-readiness/ephemeral-tests/plan", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def plan_ephemeral_test_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Generates a proposed resource plan -- pure local computation, NO
    Azure CLI call and NO resource creation. The admin reviews this
    (resource types, region, estimated cost, generated resource-group
    name) before ever approving it."""
    auth_result = require_permission(req, "azure.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    resource_plan_id = body.get("resource_plan_id")
    region = body.get("region")
    if not resource_plan_id or not region:
        return _error_response("'resource_plan_id' and 'region' are required.", status_code=400)

    try:
        plan = ephemeral_test_runner.generate_plan(resource_plan_id, region, actor["username"])
    except ValueError as e:
        return _error_response(str(e), status_code=400)

    try:
        saved = repositories.create_ephemeral_test_run(plan)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("ephemeral_test_plan_generated", actor, run_id=plan["run_id"], resource_plan_id=resource_plan_id, region=region)
    return _json_response(saved, status_code=201)


@bp.route(route="manage/azure-readiness/ephemeral-tests/{runId}/approve", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def approve_ephemeral_test_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Explicit human approval -- required before execute will provision
    anything (see ephemeral_test_runner.execute_run's approved check)."""
    auth_result = require_permission(req, "azure.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    run_id = req.route_params.get("runId")
    try:
        run = repositories.get_ephemeral_test_run(run_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if run is None:
        return _error_response(f"Test run '{run_id}' not found.", status_code=404)
    if run["status"] != "AWAITING_APPROVAL":
        return _error_response(f"Run is in status '{run['status']}', not AWAITING_APPROVAL.", status_code=400)

    run["approved"] = True
    run["approved_by"] = actor["username"]
    try:
        saved = repositories.update_ephemeral_test_run(run)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("ephemeral_test_approved", actor, run_id=run_id)
    return _json_response(saved)


@bp.route(route="manage/azure-readiness/ephemeral-tests/{runId}/execute", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def execute_ephemeral_test_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Provisions the approved plan's resources (a fresh resource group
    only) and runs its one functional test. Requires prior approval --
    see ephemeral_test_runner.execute_run()'s docstring for the full,
    honestly-fail-closed step sequence (az CLI auth check first, then
    resource-group-collision check, then the actual deployment)."""
    auth_result = require_permission(req, "azure.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    run_id = req.route_params.get("runId")
    try:
        run = repositories.get_ephemeral_test_run(run_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if run is None:
        return _error_response(f"Test run '{run_id}' not found.", status_code=404)

    with observability.trace_stage("azure_ephemeral_test", "execute", metadata={"run_id": run_id}, correlation_id=run_id):
        run = ephemeral_test_runner.execute_run(run, actor["username"])

    try:
        saved = repositories.update_ephemeral_test_run(run)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("ephemeral_test_executed", actor, run_id=run_id, status=run["status"])
    return _json_response(saved)


@bp.route(route="manage/azure-readiness/ephemeral-tests/{runId}/cleanup", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def cleanup_ephemeral_test_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Deletes ONLY this run's own resource group (ownership-tag-checked
    first -- see ephemeral_test_runner.cleanup_run()). Safe to call
    multiple times: CLEANUP_IN_PROGRESS means call it again to keep
    polling, CLEANUP_VERIFIED means it's confirmed gone, CLEANUP_FAILED
    means a remaining resource needs manual attention (reported, never
    hidden)."""
    auth_result = require_permission(req, "azure.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    run_id = req.route_params.get("runId")
    try:
        run = repositories.get_ephemeral_test_run(run_id)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if run is None:
        return _error_response(f"Test run '{run_id}' not found.", status_code=404)

    run = ephemeral_test_runner.cleanup_run(run, actor["username"])
    try:
        saved = repositories.update_ephemeral_test_run(run)
    except Exception as e:
        return _cosmos_unavailable_response(e)

    _audit_admin_action("ephemeral_test_cleanup", actor, run_id=run_id, cleanup_status=run.get("cleanup_status"))
    return _json_response(saved)


# ---------------------------------------------------------------------------
# Secrets Management (/admin/secrets). A read-only inventory of every
# secret this app actually reads (secrets_registry.SECRET_DEFINITIONS,
# verified against real get_secret()/_require_secrets() call sites, not
# invented) -- presence and source (Key Vault vs .env) only, NEVER a
# value. There is no write endpoint here on purpose: setting a secret's
# VALUE happens outside this app, via your .env file or Key Vault
# directly (see secrets_loader.py) -- the same rule azure_integrations.py
# already enforces for Azure integration config.
# ---------------------------------------------------------------------------
@bp.route(route="manage/secrets", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_secrets_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Every secret this app reads, whether it currently resolves, and
    from where -- never the value. `vault_configured` reflects whether
    AZURE_KEY_VAULT_URL is set at all (independent of any individual
    secret's resolution), so the UI can distinguish "Key Vault isn't set
    up" from "this one secret just isn't there."""
    auth_result = require_permission(req, "secrets.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    import os
    secrets = secrets_registry.list_secret_status()
    return _json_response({
        "count": len(secrets), "vault_configured": bool(os.environ.get("AZURE_KEY_VAULT_URL")),
        "secrets": secrets,
    })


@bp.route(route="manage/secrets/{key}", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def set_secret_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Writes ONE secret's value to the repo's .env file (secrets_registry.
    update_secret_value() -> secrets_loader.set_env_secret()) -- the ONLY
    write path offered anywhere in this app's secret handling, and the
    ONLY place a raw credential value is ever accepted through an API.
    `key` must be one of secrets_registry.SECRET_KEYS (never an
    arbitrary environment variable name). The value is NEVER echoed
    back, logged, or included in the audit record -- only the key name
    and the resulting presence/source are."""
    auth_result = require_permission(req, "secrets.write")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    key = req.route_params.get("key")
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    value = body.get("value")
    if not value or not isinstance(value, str):
        return _error_response("'value' is required and must be a non-empty string.", status_code=400)

    try:
        updated = secrets_registry.update_secret_value(key, value)
    except ValueError as e:
        return _error_response(str(e), status_code=400)
    _audit_admin_action("secret_value_set", actor, key=key)
    return _json_response(updated)


# ---------------------------------------------------------------------------
# RAG Configuration Console (/admin/rag-config*). See rag_config.py's
# module docstring for the full architecture rationale: a real, versioned
# config for the resume-matching RAG pipeline (ResumeParser.py), reusing
# the same Draft -> Validate -> Activate -> Rollback lifecycle already
# built for Azure integrations, plus REAL retrieval/generation preview
# against the actual on-disk semantic index (rag_preview.py) -- never a
# simulated result.
# ---------------------------------------------------------------------------
@bp.route(route="manage/rag-config", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_rag_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Active config, pending draft (if any), and the REAL knowledge-
    source status (configured/connected/indexed/retrievable -- never
    collapsed into one status, same rule as the Azure Service Hub)."""
    auth_result = require_permission(req, "rag.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        active = repositories.get_active_rag_config()
        draft = repositories.get_draft_rag_config()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    source_status = rag_preview.get_source_status()
    return _json_response({
        "active": active or dict(rag_config.DEFAULT_CONFIG, id="active", config_version=0, note="Never activated -- ResumeParser.py is running on its built-in hardcoded defaults."),
        "draft": draft, "source_status": source_status,
    })


@bp.route(route="manage/rag-config/validate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def validate_rag_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "rag.config.write")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    errors = rag_config.validate_config(body)
    return _json_response({"valid": not errors, "errors": errors})


@bp.route(route="manage/rag-config/draft", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def save_rag_config_draft_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Body: {chunking: {...}, retrieval: {...}, generation: {...}} -- the
    full shape (see rag_config.DEFAULT_CONFIG). Rejected with field-level
    errors if out of the validated ranges; a rejected draft NEVER
    overwrites the active configuration."""
    auth_result = require_permission(req, "rag.config.write")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    for section in ("chunking", "retrieval", "generation"):
        if section not in body or not isinstance(body[section], dict):
            return _error_response(f"'{section}' is required and must be an object.", status_code=400)

    errors = rag_config.validate_config(body)
    if errors:
        return _error_response("Invalid configuration: " + " ".join(errors), status_code=400)

    try:
        saved = repositories.save_draft_rag_config(body, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("rag_config_draft_saved", actor, config_version=saved["config_version"])
    return _json_response(saved)


@bp.route(route="manage/rag-config/activate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def activate_rag_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Re-validates the current draft (defense in depth -- never trusts
    that a previously-saved draft is still valid against the latest
    rules) before promoting it. The draft is consumed (deleted) on
    success.

    If the draft's chunking.max_chars differs from the currently active
    configuration, this is a REINDEXING-IMPACT change -- existing
    embeddings/index entries were built against the OLD chunk
    boundaries and do not reflect the new ones until resumes are
    reprocessed (this endpoint never reprocesses anything itself; see
    rag_config.chunking_changed()'s docstring). Activation is refused
    with 409 unless the request body explicitly sets
    "acknowledge_reindex_impact": true -- the spec's "require explicit
    confirmation for changes that trigger reindexing," enforced
    structurally rather than left to a UI dialog alone."""
    auth_result = require_permission(req, "rag.config.activate")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json() if req.get_body() else {}
    except ValueError:
        body = {}
    acknowledge_reindex_impact = bool((body or {}).get("acknowledge_reindex_impact", False))

    try:
        draft = repositories.get_draft_rag_config()
        active = repositories.get_active_rag_config()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if draft is None:
        return _error_response("No draft configuration exists to activate.", status_code=404)
    errors = rag_config.validate_config(draft)
    if errors:
        return _error_response("Cannot activate an invalid draft: " + " ".join(errors), status_code=400)

    if rag_config.chunking_changed(draft, active or rag_config.DEFAULT_CONFIG) and not acknowledge_reindex_impact:
        return _error_response(
            "This draft changes chunking.max_chars, which does not retroactively apply to resumes already "
            "indexed -- their embeddings were built against the old chunk boundaries and will not reflect "
            "this change until those resumes are reprocessed (re-run ResumeParser.py; this console does not "
            "do that automatically). Resend with {\"acknowledge_reindex_impact\": true} to activate anyway.",
            status_code=409,
        )

    try:
        activated = repositories.activate_rag_config(actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action(
        "rag_config_activated", actor, config_version=activated["config_version"],
        reindex_impact_acknowledged=acknowledge_reindex_impact,
    )
    return _json_response(activated)


@bp.route(route="manage/rag-config/history", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_rag_config_history_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "rag.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        limit = min(int(req.params.get("limit", 50)), 200)
    except ValueError:
        return _error_response("'limit' must be an integer.", status_code=400)
    try:
        history = repositories.list_rag_config_history(limit=limit)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(history), "history": history})


@bp.route(route="manage/rag-config/rollback/{history_id}", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def rollback_rag_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Restores a prior ACTIVATED configuration snapshot as a NEW draft
    -- never activates it directly, so an admin must explicitly re-
    activate (and the pipeline re-validates) rather than a rollback
    silently going live. History itself is never mutated."""
    auth_result = require_permission(req, "rag.config.activate")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    history_id = req.route_params.get("history_id")
    try:
        history = repositories.list_rag_config_history(limit=200)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    entry = next((h for h in history if h["id"] == history_id), None)
    if entry is None or entry["operation"] != "activated":
        return _error_response("No activated configuration snapshot found for that history entry.", status_code=404)

    try:
        saved = repositories.save_draft_rag_config({
            "chunking": entry["safe_summary"]["chunking"], "retrieval": entry["safe_summary"]["retrieval"],
            "generation": entry["safe_summary"]["generation"],
        }, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("rag_config_rolled_back_to_draft", actor, rolled_back_to_history_id=history_id, new_draft_version=saved["config_version"])
    return _json_response(saved)


@bp.route(route="manage/rag-config/prompts", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_rag_prompts_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Every real prompt this RAG pipeline uses, with every version ever
    created -- see rag_config.PROMPT_IDS for exactly which two (verified
    against ResumeParser.py's real call sites, not the full prompts/
    registry.json, most of which belongs to other, non-RAG workflows
    like interview scoring)."""
    auth_result = require_permission(req, "rag.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        prompts = {pid: repositories.list_rag_prompt_versions(pid) for pid in rag_config.PROMPT_IDS}
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"prompts": prompts})


@bp.route(route="manage/rag-config/prompts/{prompt_id}/draft", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def create_rag_prompt_draft_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Body: {"template": "..."}. Validates that every input variable the
    prompt actually needs (rag_config.PROMPT_DEFAULTS[prompt_id]
    ['input_variables']) still appears as a {placeholder} -- a draft
    missing one would silently break at runtime (str.format KeyError)
    the first time it's used, so this is checked up front instead."""
    auth_result = require_permission(req, "rag.prompts.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    prompt_id = req.route_params.get("prompt_id")
    if prompt_id not in rag_config.PROMPT_IDS:
        return _error_response(f"Unknown prompt_id '{prompt_id}'.", status_code=404)
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    template = body.get("template")
    if not template or not isinstance(template, str):
        return _error_response("'template' is required and must be a non-empty string.", status_code=400)

    missing_vars = [v for v in rag_config.PROMPT_DEFAULTS[prompt_id]["input_variables"] if "{" + v + "}" not in template]
    if missing_vars:
        return _error_response(f"Template is missing required placeholder(s): {missing_vars}.", status_code=400)

    try:
        saved = repositories.create_rag_prompt_draft_version(prompt_id, template, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("rag_prompt_draft_created", actor, prompt_id=prompt_id, version=saved["version"])
    return _json_response(saved, status_code=201)


@bp.route(route="manage/rag-config/prompts/{prompt_id}/versions/{version}/activate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def activate_rag_prompt_version_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "rag.prompts.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    prompt_id = req.route_params.get("prompt_id")
    if prompt_id not in rag_config.PROMPT_IDS:
        return _error_response(f"Unknown prompt_id '{prompt_id}'.", status_code=404)
    try:
        version = int(req.route_params.get("version"))
    except (TypeError, ValueError):
        return _error_response("'version' must be an integer.", status_code=400)

    try:
        saved = repositories.activate_rag_prompt_version(prompt_id, version, actor["username"])
    except ValueError as e:
        return _error_response(str(e), status_code=404)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("rag_prompt_version_activated", actor, prompt_id=prompt_id, version=version)
    return _json_response(saved)


_rag_preview_last_run = {}  # actor_id -> monotonic seconds, per-process only
_RAG_PREVIEW_MIN_INTERVAL_SECONDS = 5


@bp.route(route="manage/rag-config/preview", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def preview_rag_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Runs REAL retrieval (and, if requested, real generation -- genuine
    LLM calls, real cost, capped at rag_preview.MAX_GENERATION_PREVIEW_CHUNKS)
    against the actual on-disk semantic index, using either the pending
    draft's parameters or the active configuration's -- never a
    simulated result. Body: {"jd_text": "...", "use": "draft"|"active"
    (default "active"), "top_k": optional override, "include_generation":
    bool (default false, since it makes real billable LLM calls),
    "compare": bool (default false) -- when true, runs retrieval against
    BOTH the active and draft configurations' retrieval parameters and
    returns both (compare never runs generation, to avoid doubling LLM
    cost on one preview click), so an admin can see exactly how a draft
    change would shift results before activating it.
    Rate-limited per actor since generation previews cost real money."""
    auth_result = require_permission(req, "rag.preview.run")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    jd_text = (body.get("jd_text") or "").strip()
    if not jd_text:
        return _error_response("'jd_text' is required.", status_code=400)
    use = body.get("use", "active")
    include_generation = bool(body.get("include_generation", False))
    compare = bool(body.get("compare", False))

    if _rate_limited(actor["id"], _RAG_PREVIEW_MIN_INTERVAL_SECONDS, store=_rag_preview_last_run):
        return _error_response(f"Previewed too recently -- wait at least {_RAG_PREVIEW_MIN_INTERVAL_SECONDS}s between previews.", status_code=429)

    try:
        active_config = repositories.get_active_rag_config() or rag_config.DEFAULT_CONFIG
        draft_config = repositories.get_draft_rag_config()
    except Exception as e:
        return _cosmos_unavailable_response(e)

    if compare:
        try:
            active_retrieval = active_config.get("retrieval", rag_config.DEFAULT_CONFIG["retrieval"])
            compare_result = {"active": rag_preview.run_retrieval_preview(jd_text, active_retrieval.get("top_k", 5), active_retrieval.get("similarity_threshold", 0.0)), "draft": None}
            if draft_config is not None:
                draft_retrieval = draft_config.get("retrieval", rag_config.DEFAULT_CONFIG["retrieval"])
                compare_result["draft"] = rag_preview.run_retrieval_preview(jd_text, draft_retrieval.get("top_k", 5), draft_retrieval.get("similarity_threshold", 0.0))
        except FileNotFoundError as e:
            return _error_response(str(e), status_code=409)
        except Exception as e:
            return _error_response(f"Retrieval preview failed: {e}", status_code=502)
        _audit_admin_action("rag_config_previewed", actor, compare=True, had_draft=draft_config is not None)
        return _json_response({"compare": compare_result, "retrieval": None, "generation": None})

    source_config = (draft_config if use == "draft" else active_config) or rag_config.DEFAULT_CONFIG
    retrieval_cfg = source_config.get("retrieval", rag_config.DEFAULT_CONFIG["retrieval"])
    top_k = int(body.get("top_k") or retrieval_cfg.get("top_k", 5))
    similarity_threshold = float(retrieval_cfg.get("similarity_threshold", 0.0))

    try:
        retrieval_result = rag_preview.run_retrieval_preview(jd_text, top_k, similarity_threshold)
    except FileNotFoundError as e:
        return _error_response(str(e), status_code=409)
    except Exception as e:
        return _error_response(f"Retrieval preview failed: {e}", status_code=502)

    result = {"retrieval": retrieval_result, "generation": None, "compare": None}
    if include_generation and not retrieval_result["insufficient_evidence"]:
        try:
            chunk_prompt = rag_config.get_active_prompt_template("resume_chunk_summarization")
            merge_prompt = rag_config.get_active_prompt_template("resume_merge_structured_extraction")
            generation_cfg = source_config.get("generation", rag_config.DEFAULT_CONFIG["generation"])
            result["generation"] = rag_preview.run_generation_preview(
                jd_text, retrieval_result["chunks"], chunk_prompt, merge_prompt, generation_cfg,
            )
        except Exception as e:
            result["generation"] = {"error": f"Generation preview failed: {e}"}
    elif include_generation and retrieval_result["insufficient_evidence"]:
        result["generation"] = {"error": "Skipped -- no retrieved evidence passed the similarity threshold."}

    _audit_admin_action(
        "rag_config_previewed", actor, use=use, top_k=top_k, chunks_retrieved=len(retrieval_result["chunks"]),
        included_generation=include_generation,
    )
    return _json_response(result)


# ---------------------------------------------------------------------------
# Communications Configuration Console (/admin/communications-config*).
# See comms_config.py's module docstring for the full architecture
# rationale: a real channel-adapter registry (email/whatsapp supported,
# sms honestly labeled absent), the same Draft -> Validate -> Activate ->
# Rollback lifecycle already built twice this session, a SEPARATE
# lifecycle for the interview bot (per the spec's "keep them separate"),
# versioned message templates seeded from real existing templates, and a
# real delivery log + duplicate-send protection wired into the actual
# WhatsApp notification call site (_notify_candidate_whatsapp above).
# ---------------------------------------------------------------------------
_comms_test_last_run = {}  # actor_id -> monotonic seconds, per-process only
_COMMS_TEST_MIN_INTERVAL_SECONDS = 10


@bp.route(route="manage/communications-config", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_comms_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        active = repositories.get_active_comms_config()
        draft = repositories.get_draft_comms_config()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({
        "active": active or dict(comms_config.DEFAULT_COMMS_CONFIG, id="active", config_version=0, note="Never activated -- real send paths use their built-in defaults."),
        "draft": draft, "channels": list(comms_config.CHANNELS.values()),
    })


@bp.route(route="manage/communications-config/validate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def validate_comms_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.config.write")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    errors = comms_config.validate_comms_config(body)
    return _json_response({"valid": not errors, "errors": errors})


@bp.route(route="manage/communications-config/draft", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def save_comms_config_draft_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.config.write")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    for section in ("email", "whatsapp", "delivery_policies"):
        if section not in body or not isinstance(body[section], dict):
            return _error_response(f"'{section}' is required and must be an object.", status_code=400)

    errors = comms_config.validate_comms_config(body)
    if errors:
        return _error_response("Invalid configuration: " + " ".join(errors), status_code=400)

    try:
        saved = repositories.save_draft_comms_config(body, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("comms_config_draft_saved", actor, config_version=saved["config_version"])
    return _json_response(saved)


@bp.route(route="manage/communications-config/activate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def activate_comms_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.config.activate")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        draft = repositories.get_draft_comms_config()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if draft is None:
        return _error_response("No draft configuration exists to activate.", status_code=404)
    errors = comms_config.validate_comms_config(draft)
    if errors:
        return _error_response("Cannot activate an invalid draft: " + " ".join(errors), status_code=400)

    try:
        activated = repositories.activate_comms_config(actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("comms_config_activated", actor, config_version=activated["config_version"])
    return _json_response(activated)


@bp.route(route="manage/communications-config/history", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_comms_config_history_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        limit = min(int(req.params.get("limit", 50)), 200)
    except ValueError:
        return _error_response("'limit' must be an integer.", status_code=400)
    try:
        history = repositories.list_comms_config_history(limit=limit)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(history), "history": history})


@bp.route(route="manage/communications-config/rollback/{history_id}", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def rollback_comms_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Restores a prior ACTIVATED snapshot as a NEW draft -- never
    activates it directly, same pattern as the Azure/RAG consoles."""
    auth_result = require_permission(req, "comms.config.activate")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    history_id = req.route_params.get("history_id")
    try:
        history = repositories.list_comms_config_history(limit=200)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    entry = next((h for h in history if h["id"] == history_id and h["operation"] == "activated"), None)
    if entry is None:
        return _error_response("No activated configuration snapshot found for that history entry.", status_code=404)

    try:
        saved = repositories.save_draft_comms_config({
            "email": entry["safe_summary"]["email"], "whatsapp": entry["safe_summary"]["whatsapp"],
            "delivery_policies": entry["safe_summary"]["delivery_policies"],
        }, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("comms_config_rolled_back_to_draft", actor, rolled_back_to_history_id=history_id, new_draft_version=saved["config_version"])
    return _json_response(saved)


@bp.route(route="manage/communications-config/interview-bot", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_interview_bot_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        active = repositories.get_active_interview_bot_config()
        draft = repositories.get_draft_interview_bot_config()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({
        "active": active or dict(comms_config.DEFAULT_INTERVIEW_BOT_CONFIG, id="active", config_version=0, note="Never activated -- voicebot/voice_bot.py is running on its built-in hardcoded defaults."),
        "draft": draft,
    })


@bp.route(route="manage/communications-config/interview-bot/draft", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def save_interview_bot_config_draft_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Only exposes the real tunable fields (model/temperature/max_tokens/
    warmup_question_count/language) -- never arbitrary system-prompt
    text or code, per the spec's explicit "do not expose unrestricted
    system-prompt modification" rule."""
    auth_result = require_permission(req, "comms.config.write")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    fields = {k: body.get(k, v) for k, v in comms_config.DEFAULT_INTERVIEW_BOT_CONFIG.items()}
    errors = comms_config.validate_interview_bot_config(fields)
    if errors:
        return _error_response("Invalid configuration: " + " ".join(errors), status_code=400)

    try:
        saved = repositories.save_draft_interview_bot_config(fields, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("interview_bot_config_draft_saved", actor, config_version=saved["config_version"])
    return _json_response(saved)


@bp.route(route="manage/communications-config/interview-bot/activate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def activate_interview_bot_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.config.activate")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        draft = repositories.get_draft_interview_bot_config()
    except Exception as e:
        return _cosmos_unavailable_response(e)
    if draft is None:
        return _error_response("No draft interview bot configuration exists to activate.", status_code=404)
    errors = comms_config.validate_interview_bot_config(draft)
    if errors:
        return _error_response("Cannot activate an invalid draft: " + " ".join(errors), status_code=400)

    try:
        activated = repositories.activate_interview_bot_config(actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("interview_bot_config_activated", actor, config_version=activated["config_version"])
    return _json_response(activated)


@bp.route(route="manage/communications-config/templates", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def list_comms_templates_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    try:
        templates = {tid: repositories.list_comms_template_versions(tid) for tid in comms_config.TEMPLATE_IDS}
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"templates": templates})


@bp.route(route="manage/communications-config/templates/{template_id}/draft", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def create_comms_template_draft_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.templates.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    template_id = req.route_params.get("template_id")
    defaults = comms_config.DEFAULT_TEMPLATES.get(template_id)
    if defaults is None:
        return _error_response(f"Unknown template_id '{template_id}'.", status_code=404)
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    body_text = body.get("body")
    subject = body.get("subject", "")
    if not body_text or not isinstance(body_text, str):
        return _error_response("'body' is required and must be a non-empty string.", status_code=400)

    missing_vars = [v for v in defaults["input_variables"] if "{" + v + "}" not in body_text]
    if missing_vars:
        return _error_response(f"Template body is missing required placeholder(s): {missing_vars}.", status_code=400)

    try:
        saved = repositories.create_comms_template_draft_version(template_id, subject, body_text, actor["username"])
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("comms_template_draft_created", actor, template_id=template_id, version=saved["version"])
    return _json_response(saved, status_code=201)


@bp.route(route="manage/communications-config/templates/{template_id}/versions/{version}/activate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def activate_comms_template_version_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.templates.manage")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    template_id = req.route_params.get("template_id")
    if template_id not in comms_config.TEMPLATE_IDS:
        return _error_response(f"Unknown template_id '{template_id}'.", status_code=404)
    try:
        version = int(req.route_params.get("version"))
    except (TypeError, ValueError):
        return _error_response("'version' must be an integer.", status_code=400)

    try:
        saved = repositories.activate_comms_template_version(template_id, version, actor["username"])
    except ValueError as e:
        return _error_response(str(e), status_code=404)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    _audit_admin_action("comms_template_version_activated", actor, template_id=template_id, version=version)
    return _json_response(saved)


@bp.route(route="manage/communications-config/templates/{template_id}/preview", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def preview_comms_template_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Renders the ACTIVE template version against clearly-labeled
    SYNTHETIC example data (comms_config.TEMPLATE_PREVIEW_EXAMPLE_DATA)
    -- never a real candidate's name, email, phone, or interview
    details, per the spec's explicit "do not expose real candidate
    personal information in template previews" rule."""
    auth_result = require_permission(req, "comms.config.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    template_id = req.route_params.get("template_id")
    try:
        template = comms_config.get_active_template(template_id)
    except ValueError as e:
        return _error_response(str(e), status_code=404)
    try:
        rendered_subject = template.get("subject", "").format(**comms_config.TEMPLATE_PREVIEW_EXAMPLE_DATA)
        rendered_body = template["body"].format(**comms_config.TEMPLATE_PREVIEW_EXAMPLE_DATA)
    except KeyError as e:
        return _error_response(f"Template references placeholder {e} which isn't available to this workflow.", status_code=400)
    return _json_response({
        "template_id": template_id, "version": template["version"], "is_synthetic_example_data": True,
        "rendered_subject": rendered_subject, "rendered_body": rendered_body,
    })


@bp.route(route="manage/communications-config/email/test-connectivity", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def test_email_connectivity_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    auth_result = require_permission(req, "comms.test.send")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    if _rate_limited(actor["id"], _COMMS_TEST_MIN_INTERVAL_SECONDS, store=_comms_test_last_run):
        return _error_response(f"Tested too recently -- wait at least {_COMMS_TEST_MIN_INTERVAL_SECONDS}s between tests.", status_code=429)
    result = email_adapter.test_connectivity()
    _audit_admin_action("comms_email_connectivity_tested", actor, outcome=result["outcome"])
    return _json_response(result)


@bp.route(route="manage/communications-config/email/test-send", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def send_test_email_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Sends ONE real email to an EXPLICITLY chosen recipient -- requires
    both "to" and "confirm": true in the body; refuses otherwise. The
    subject/body are always prefixed/labeled as a test, regardless of
    what the caller passes, so a test message can never be mistaken for
    a real notification by whoever receives it."""
    auth_result = require_permission(req, "comms.test.send")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be JSON.", status_code=400)
    to_address = (body.get("to") or "").strip()
    if not to_address:
        return _error_response("'to' (an explicit recipient email address) is required.", status_code=400)
    if body.get("confirm") is not True:
        return _error_response("'confirm': true is required to send a test email.", status_code=400)

    if _rate_limited(actor["id"], _COMMS_TEST_MIN_INTERVAL_SECONDS, store=_comms_test_last_run):
        return _error_response(f"Tested too recently -- wait at least {_COMMS_TEST_MIN_INTERVAL_SECONDS}s between tests.", status_code=429)

    subject = f"[CDPI TEST MESSAGE] {body.get('subject') or 'Communications Configuration test'}"
    message_body = (
        "This is a TEST message sent from the CDPI Communications Configuration console by "
        f"{actor['username']}. It does not represent a real candidate notification.\n\n"
        f"{body.get('body') or ''}"
    )
    try:
        result = email_adapter.send_email(to_address, subject, message_body)
    except Exception as e:
        _audit_admin_action("comms_email_test_sent", actor, to=to_address, outcome="failed")
        return _error_response(f"Test email failed to send: {e}", status_code=502)
    _audit_admin_action("comms_email_test_sent", actor, to=to_address, outcome="sent")
    return _json_response(dict(result, is_test_message=True))


@bp.route(route="manage/communications-config/whatsapp/test-connectivity", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def test_whatsapp_connectivity_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """A real Graph API metadata lookup -- never a message send (WhatsApp
    has no safe test-message path, see comms_config.CHANNELS["whatsapp"]'s
    capabilities)."""
    auth_result = require_permission(req, "comms.test.send")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    actor = auth_result
    if _rate_limited(actor["id"], _COMMS_TEST_MIN_INTERVAL_SECONDS, store=_comms_test_last_run):
        return _error_response(f"Tested too recently -- wait at least {_COMMS_TEST_MIN_INTERVAL_SECONDS}s between tests.", status_code=429)
    import whatsapp_client

    result = whatsapp_client.check_connection()
    _audit_admin_action("comms_whatsapp_connectivity_tested", actor, outcome=result["outcome"])
    return _json_response(result)


@bp.route(route="manage/communications-config/delivery-log", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_comms_delivery_log_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """Real delivery records for one candidate (correlation_id) -- never
    fabricated statistics. Distinguishes "message submitted"/"provider
    accepted"/"failed" (see _notify_candidate_whatsapp above) -- this
    repo has no delivery-status WEBHOOK wired up anywhere, so "delivered"
    (confirmed received) vs. "provider accepted" (Meta/SMTP took the
    request) are never conflated; nothing here claims the stronger
    status without real evidence for it."""
    auth_result = require_permission(req, "comms.delivery.read")
    if isinstance(auth_result, func.HttpResponse):
        return auth_result
    correlation_id = req.params.get("correlation_id")
    if not correlation_id:
        return _error_response("'correlation_id' query parameter is required.", status_code=400)
    try:
        limit = min(int(req.params.get("limit", 50)), 200)
    except ValueError:
        return _error_response("'limit' must be an integer.", status_code=400)
    try:
        deliveries = repositories.list_comms_deliveries(correlation_id, limit=limit)
    except Exception as e:
        return _cosmos_unavailable_response(e)
    return _json_response({"count": len(deliveries), "deliveries": deliveries})
