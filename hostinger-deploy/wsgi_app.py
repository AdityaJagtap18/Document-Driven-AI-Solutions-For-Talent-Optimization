"""Hostinger (Passenger/WSGI) entry point for the real CDPI backend --
NOT a rewrite of azure-functions/api_blueprint.py. It introspects the
real, unmodified `bp` Blueprint from that file (all 130+ @bp.route
endpoints, exactly as Azure would run them) and auto-registers one Flask
view per HTTP-triggered function, by constructing a real
azure.functions.HttpRequest from the incoming Flask request and handing
it to the SAME handler function Azure would call -- so every endpoint's
actual business logic (auth, Cosmos/Blob/OpenAI calls, validation) is
byte-for-byte the code already tested as azure-functions/api_blueprint.py,
not a reimplementation of it.

Why this approach instead of hand-writing 130+ Flask routes: the
`azure-functions` PyPI package (imported as `azure.functions`/`func`
throughout this repo) is just a plain Python library for building these
request/response objects -- it has no dependency on the Azure Functions
Core Tools runtime or Azure platform itself (this repo's own tests,
tests/test_api_blueprint.py, already call these handlers directly with
hand-built func.HttpRequest objects, with zero Azure connectivity).
Reusing that same object model here means this file never has to be
kept in sync by hand every time a new endpoint is added to
api_blueprint.py -- it just appears automatically on next deploy.

Real, deliberate differences from the Azure deployment (documented, not
hidden):
  1. Azure Functions' platform-level "FUNCTION" auth level (a function
     key in the URL/header) is NOT enforced here -- this repo's real
     authorization boundary has always been the JWT/require_permission()
     layer INSIDE api_blueprint.py (see that file's own docstrings),
     which is completely unchanged and still fully enforced. The
     function-key layer was Azure-platform-specific defense-in-depth on
     top of that, never the actual boundary, and was already a no-op
     during every local `func start` run this whole engagement used.
  2. The Blob-triggered resume_blob_trigger (function_app.py) has no
     Hostinger equivalent -- replaced by resume_blob_poller.py, meant to
     run as a Hostinger Cron Job. See that file's own docstring.
  3. CORS is handled here explicitly (an `after_request` hook) since
     there is no Azure Functions host/CORS setting on Hostinger.
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (_REPO_ROOT, _REPO_ROOT / "context-lake", _REPO_ROOT / "azure-functions"):
    sys.path.insert(0, str(_p))

import azure.functions as func  # noqa: E402
from flask import Flask, request, Response  # noqa: E402

import api_blueprint  # noqa: E402

application = Flask(__name__)
# Resume uploads are base64-encoded PDFs in a JSON body -- a few MB is a
# real, expected request size here, not an attack; 16MB is generous
# headroom over anything this app's own real resumes have been.
application.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

_ALLOWED_ORIGIN = os.environ.get("CDPI_CORS_ALLOWED_ORIGIN", "*")


def _azure_route_to_flask(route: str) -> str:
    """Azure's `{name}` path-param syntax is already Flask-compatible as
    `<name>` with one real difference: Azure allows a `{name:type}`
    constraint (e.g. `{candidate_id}` has none here, but the syntax
    exists) -- api_blueprint.py never uses the typed form, so a plain
    rename covers every real route in this app."""
    return "/api/" + route.replace("{", "<").replace("}", ">")


def _make_view(handler, route_param_names):
    def view(**kwargs):
        route_params = {k: v for k, v in kwargs.items()}
        azure_req = func.HttpRequest(
            method=request.method,
            url=request.url,
            headers=dict(request.headers),
            params=dict(request.args),
            route_params=route_params,
            body=request.get_data() or b"",
        )
        azure_resp = handler(azure_req)
        return Response(
            response=azure_resp.get_body(),
            status=azure_resp.status_code,
            headers=dict(azure_resp.headers),
            mimetype=azure_resp.mimetype,
        )
    return view


def _register_routes(app: Flask, blueprint) -> int:
    count = 0
    for builder in blueprint._function_builders:  # noqa: SLF001 -- see module docstring for why
        built = builder.build()
        if not built.is_http_function():
            continue  # the blob trigger isn't an HTTP function -- see resume_blob_poller.py instead
        trigger = built.get_trigger()
        flask_path = _azure_route_to_flask(trigger.route or built.get_function_name())
        methods = [m.value if hasattr(m, "value") else str(m) for m in trigger.methods]
        handler = built.get_user_function()
        endpoint_name = built.get_function_name()
        route_param_names = [seg[1:-1].split(":")[0] for seg in (trigger.route or "").split("/") if seg.startswith("{")]
        app.add_url_rule(
            flask_path, endpoint=endpoint_name,
            view_func=_make_view(handler, route_param_names),
            methods=methods + (["OPTIONS"] if "OPTIONS" not in methods else []),
        )
        count += 1
    return count


@application.before_request
def _short_circuit_cors_preflight():
    if request.method == "OPTIONS":
        return Response(status=204)


@application.after_request
def _add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = _ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    return resp


@application.route("/")
def _root_health():
    # Hostinger's Python-app setup hits "/" to confirm the app started --
    # the real health check is GET /api/health (api_blueprint.py's own).
    return {"status": "ok", "see": "/api/health"}


_registered = _register_routes(application, api_blueprint.bp)
print(f"[wsgi_app] registered {_registered} real api_blueprint.py HTTP routes", file=sys.stderr)
