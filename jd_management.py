"""Job Description Management: create, upload, AI-generate, edit, submit,
approve, reject, publish, and archive JDs -- the canonical Cosmos-backed
record (context-lake/schema/jd.schema.json), distinct from jd_manager.py's
existing read-only jd_folder/*.txt scanner.

PUBLISH is the only bridge between the two: publish_jd() writes
jd_folder/{jd_id}.txt so the existing, already-verified resume-scoring
pipeline (jd_manager.list_jds() / ResumeMEtrics.py) picks it up unchanged --
draft/pending/rejected/archived JDs are never written there, so they never
affect candidate matching. This was a deliberate scope decision: migrating
the scoring pipeline itself to read from Cosmos was out of scope (real risk
to an already end-to-end-verified pipeline) -- see
docs/JD_MANAGEMENT.md for the full rationale.

Lifecycle: draft -> pending_approval -> approved -> published -> archived,
with pending_approval -> rejected -> draft as the correction loop. Every
transition is validated here (never trust the frontend to only send legal
transitions) and recorded to jd_audit_log via repositories.append_jd_audit_log().
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "context-lake"))

import repositories  # noqa: E402
import observability  # noqa: E402

JD_FOLDER = _REPO_ROOT / "jd_folder"

STATUSES = ("draft", "pending_approval", "approved", "published", "rejected", "archived")

# Legal transitions: current_status -> {action: next_status}
_TRANSITIONS = {
    "draft": {"submit": "pending_approval"},
    "pending_approval": {"approve": "approved", "reject": "rejected"},
    "approved": {"publish": "published"},
    "rejected": {"submit": "pending_approval"},
    "published": {"archive": "archived"},
    "archived": {},
}

EDITABLE_STATUSES = ("draft", "rejected")

_REQUIRED_FOR_SUBMIT = ("title", "description")

TEXT_FIELDS = (
    "title", "description", "required_experience", "seniority_level", "employment_type",
    "location", "work_arrangement", "department", "salary_range", "hiring_contact",
    "application_deadline", "additional_requirements",
)
LIST_FIELDS = ("responsibilities", "required_qualifications", "preferred_qualifications", "required_skills", "preferred_skills")


def _now():
    return datetime.now(timezone.utc).isoformat()


class JdValidationError(Exception):
    pass


class JdTransitionError(Exception):
    pass


def _clean_fields(fields: dict) -> dict:
    """Keeps only known JD fields, coercing list-fields to real lists --
    never trusts the caller to have sent exactly the right shape."""
    cleaned = {}
    for key in TEXT_FIELDS:
        if key in fields and fields[key] is not None:
            cleaned[key] = str(fields[key]).strip()
    for key in LIST_FIELDS:
        if key in fields and fields[key] is not None:
            value = fields[key]
            cleaned[key] = [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else [str(value).strip()]
    if "openings_count" in fields and fields["openings_count"] is not None:
        try:
            cleaned["openings_count"] = max(1, int(fields["openings_count"]))
        except (TypeError, ValueError):
            raise JdValidationError("'openings_count' must be a whole number.")
    return cleaned


def create_jd(fields: dict, created_by: str, source: str = "manual",
              source_document_blob_path: str = None, ai_generation_metadata: dict = None) -> dict:
    if source not in ("manual", "uploaded", "ai_generated"):
        raise JdValidationError(f"Invalid source '{source}'.")
    cleaned = _clean_fields(fields)
    if not cleaned.get("title"):
        raise JdValidationError("'title' is required.")
    jd_id = str(uuid.uuid4())
    jd = dict(cleaned)
    jd.update({
        "jd_id": jd_id, "status": "draft", "source": source,
        "created_by": created_by, "created_at": _now(),
    })
    if source_document_blob_path:
        jd["source_document_blob_path"] = source_document_blob_path
    if ai_generation_metadata:
        jd["ai_generation_metadata"] = ai_generation_metadata
    repositories.create_jd(jd)
    _audit(jd_id, "created", created_by, detail=f"source={source}")
    return jd


def update_jd(jd_id: str, fields: dict, updated_by: str) -> dict:
    jd = repositories.get_jd(jd_id)
    if jd is None:
        raise JdTransitionError(f"JD '{jd_id}' not found.")
    if jd["status"] not in EDITABLE_STATUSES:
        raise JdTransitionError(f"JD is '{jd['status']}' -- only {EDITABLE_STATUSES} JDs can be edited.")
    cleaned = _clean_fields(fields)
    jd.update(cleaned)
    jd["updated_by"] = updated_by
    repositories.update_jd(jd)
    _audit(jd_id, "updated", updated_by)
    return jd


def _transition(jd_id: str, action: str, actor: str, extra_fields: dict = None) -> dict:
    jd = repositories.get_jd(jd_id)
    if jd is None:
        raise JdTransitionError(f"JD '{jd_id}' not found.")
    legal = _TRANSITIONS.get(jd["status"], {})
    if action not in legal:
        raise JdTransitionError(f"Cannot '{action}' a JD that is '{jd['status']}'. Legal actions from this state: {list(legal) or 'none'}.")
    jd["status"] = legal[action]
    if extra_fields:
        jd.update(extra_fields)
    repositories.update_jd(jd)
    _audit(jd_id, action, actor)
    return jd


def submit_jd(jd_id: str, submitted_by: str) -> dict:
    jd = repositories.get_jd(jd_id)
    if jd is None:
        raise JdTransitionError(f"JD '{jd_id}' not found.")
    missing = [f for f in _REQUIRED_FOR_SUBMIT if not jd.get(f)]
    if missing:
        raise JdValidationError(f"Cannot submit for approval -- missing required field(s): {', '.join(missing)}.")
    return _transition(jd_id, "submit", submitted_by, {"submitted_by": submitted_by, "submitted_at": _now()})


def approve_jd(jd_id: str, approved_by: str) -> dict:
    return _transition(jd_id, "approve", approved_by, {"approved_by": approved_by, "approved_at": _now()})


def reject_jd(jd_id: str, rejected_by: str, reason: str) -> dict:
    if not (reason or "").strip():
        raise JdValidationError("A rejection reason is required.")
    return _transition(jd_id, "reject", rejected_by, {"rejected_by": rejected_by, "rejected_at": _now(), "rejection_reason": reason.strip()})


def compose_jd_text(jd: dict) -> str:
    """Builds the flat .txt body jd_manager.py's _extract_title()/scoring
    pipeline expects -- "Job Title: ..." on its own line (the convention
    this repo's pre-existing JD files already use), the rest in plain
    readable sections."""
    lines = [f"Job Title: {jd.get('title', '')}", ""]
    if jd.get("department"):
        lines.append(f"Department: {jd['department']}")
    if jd.get("location"):
        lines.append(f"Location: {jd['location']}" + (f" ({jd['work_arrangement']})" if jd.get("work_arrangement") else ""))
    if jd.get("employment_type"):
        lines.append(f"Employment Type: {jd['employment_type']}")
    if jd.get("seniority_level"):
        lines.append(f"Seniority: {jd['seniority_level']}")
    lines.append("")
    if jd.get("description"):
        lines.extend(["About the Role:", jd["description"], ""])
    if jd.get("responsibilities"):
        lines.append("Responsibilities:")
        lines.extend(f"- {r}" for r in jd["responsibilities"])
        lines.append("")
    if jd.get("required_qualifications") or jd.get("required_skills"):
        lines.append("Required Qualifications & Skills:")
        lines.extend(f"- {q}" for q in jd.get("required_qualifications", []))
        lines.extend(f"- {s}" for s in jd.get("required_skills", []))
        lines.append("")
    if jd.get("preferred_qualifications") or jd.get("preferred_skills"):
        lines.append("Preferred Qualifications & Skills:")
        lines.extend(f"- {q}" for q in jd.get("preferred_qualifications", []))
        lines.extend(f"- {s}" for s in jd.get("preferred_skills", []))
        lines.append("")
    if jd.get("required_experience"):
        lines.append(f"Required Experience: {jd['required_experience']}")
    if jd.get("additional_requirements"):
        lines.extend(["", "Additional Requirements:", jd["additional_requirements"]])
    return "\n".join(lines).strip() + "\n"


def publish_jd(jd_id: str, published_by: str) -> dict:
    """approved -> published, AND syncs jd_folder/{jd_id}.txt so the
    existing resume-scoring pipeline (jd_manager.py) picks this JD up on
    its next run -- this is the only place that file gets written for a
    Cosmos-managed JD."""
    jd = _transition(jd_id, "publish", published_by, {"published_at": _now()})
    JD_FOLDER.mkdir(parents=True, exist_ok=True)
    (JD_FOLDER / f"{jd_id}.txt").write_text(compose_jd_text(jd), encoding="utf-8")
    return jd


def archive_jd(jd_id: str, archived_by: str) -> dict:
    """published -> archived, removing jd_folder/{jd_id}.txt so the
    scoring pipeline stops considering it on its next run -- this does
    NOT touch any candidate record or scoring history already computed
    against it (no silent deletion of recruitment history)."""
    jd = _transition(jd_id, "archive", archived_by, {"archived_by": archived_by, "archived_at": _now()})
    txt_path = JD_FOLDER / f"{jd_id}.txt"
    if txt_path.exists():
        txt_path.unlink()
    return jd


def _audit(jd_id: str, action: str, actor: str, detail: str = None):
    entry = {"jd_id": jd_id, "action": action, "actor": actor}
    if detail:
        entry["detail"] = detail
    try:
        repositories.append_jd_audit_log(entry)
    except Exception:
        pass  # best-effort, same fail-soft convention as the rest of this app's Cosmos writes


def extract_text_from_jd_document(file_bytes: bytes) -> str:
    """Plain PyPDF2 text extraction -- reuses the exact same generic
    pattern ResumeParser.py's extract_text_from_pdf() already uses (no
    resume-specific logic in it to begin with), just reading from bytes
    directly instead of a file path since the upload endpoint receives
    base64 bytes, not a saved file."""
    import io

    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text
    return text.strip()


def parse_extracted_jd_text(text: str) -> dict:
    """Best-effort structured extraction from raw JD text, via the same
    real LLM call pattern generate_questions.py uses (get_chat_llm() +
    call_with_retry()) -- NOT hardcoded regex parsing, since real JD
    documents vary too much in format for that to work honestly. Returns
    a dict of JD fields for the user to review/edit, never auto-saved."""
    import json

    from llm_utils import get_chat_llm, call_with_retry

    prompt = (
        "Extract structured fields from this job description document. "
        "Return ONLY a JSON object with these keys (use empty string/empty "
        "list when not present in the text -- never invent information not "
        "in the source document): title, description, responsibilities "
        "(list), required_qualifications (list), preferred_qualifications "
        "(list), required_skills (list), preferred_skills (list), "
        "required_experience, seniority_level, employment_type, location, "
        "department.\n\nDocument text:\n" + text[:8000]
    )
    import model_routing

    routed_model, _ = model_routing.resolve_model("jd_document_extraction", model_routing.get_runtime_routing_config())
    llm = get_chat_llm(temperature=0, max_tokens=1500, model=routed_model)
    response = call_with_retry(llm.invoke, prompt, label="jd_document_extraction", model=routed_model, workflow="jd_management")
    content = getattr(response, "content", str(response))
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("The AI response did not contain a parseable JSON object.")
    return json.loads(content[start:end + 1])


def generate_jd_with_ai(inputs: dict) -> dict:
    """AI-assisted JD generation from structured inputs (title, role
    purpose, responsibilities, skills, experience, seniority, department).
    Real Azure OpenAI/OpenAI call (get_chat_llm() routes to whichever is
    configured -- see llm_utils.using_azure_openai()), wrapped in a real
    AI Observability Center trace span. Returns {fields: {...}, metadata:
    {...}} -- fields are a DRAFT; nothing is persisted by this function."""
    import json

    from llm_utils import get_chat_llm, call_with_retry, using_azure_openai

    trace_id = observability.new_trace_id()
    prompt = (
        "You are drafting a job description for a recruiting team. Using "
        "only the inputs given (never invent company policies, benefits, "
        "or mandatory qualifications not implied by the inputs), produce a "
        "JSON object with these keys: title, description (2-4 sentence role "
        "summary), responsibilities (list), required_qualifications (list), "
        "preferred_qualifications (list), required_skills (list), "
        "preferred_skills (list, clearly distinct from required_skills). "
        "Return ONLY the JSON object, no other text.\n\nInputs:\n" + json.dumps(inputs, indent=2)
    )
    import model_routing

    with observability.trace_stage("jd_management", "jd_text_generation", trace_id=trace_id, metadata={"input_keys": list(inputs.keys())}):
        routed_model, _ = model_routing.resolve_model("jd_generation", model_routing.get_runtime_routing_config())
        llm = get_chat_llm(temperature=0.5, max_tokens=1200, model=routed_model)
        response = call_with_retry(
            llm.invoke, prompt, label="jd_generation", model=routed_model, workflow="jd_management", trace_id=trace_id,
        )
    content = getattr(response, "content", str(response))
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("The AI response did not contain a parseable JSON object.")
    fields = json.loads(content[start:end + 1])
    metadata = {
        "provider": "azure_openai" if using_azure_openai() else "openai",
        "model": getattr(llm, "deployment_name", None) or getattr(llm, "model_name", None) or "unknown",
        "trace_id": trace_id,
        "generated_at": _now(),
    }
    return {"fields": fields, "metadata": metadata}
