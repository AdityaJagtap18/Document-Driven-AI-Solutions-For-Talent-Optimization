"""Azure Function (Python v2 programming model), Blob-triggered.

Replaces file_watcher.py's 60-second IMAP/filesystem polling loop with a
genuine event-driven trigger: this function fires the moment a PDF lands in
the `raw-resumes` Blob container, with no polling delay.

*** NOT YET DEPLOYED OR TESTED. ***
No Azure Functions Core Tools or Azure CLI are available in this
development environment, so this has only been reviewed for correctness
against the Azure Functions Python v2 documentation and this repo's own
ResumeParser.py — it has never actually been run, locally or deployed.
Treat it as a reviewed draft, not verified working code, until someone
runs `func start` against it (see README.md in this folder) or deploys it
and triggers it with a real blob upload.

Deliberately conservative in scope: shells out to ResumeParser.py exactly
the way file_watcher.py already does today (subprocess.call), rather than
refactoring ResumeParser.py's internals to be importable — that refactor
is real additional work and risk, and this function's job is proving the
event-driven *trigger* replaces the polling loop, not rewriting the
processing logic again. file_watcher.py itself is left untouched; this is
an alternative deployment target, not a replacement, until it's been
verified against a real deployment.

KNOWN LIMITATION, not fixed here: ResumeParser.py processes every PDF
currently in InputResume_Folder in one run (input_folder.glob("*.pdf")),
not just the one that triggered this function. Two blob uploads landing
within the same processing window will each spawn a full
`python ResumeParser.py` subprocess that reprocesses the whole folder —
a real race condition under concurrent uploads (duplicate CSV rows at
minimum). Acceptable for the current scale (file_watcher.py already has
this same property today, just via polling instead of events — not a
regression), but a true fix needs either a queue (Azure Storage Queue
between the trigger and the worker) or refactoring ResumeParser.py to
process exactly one given file. Flagged here rather than silently
shipped, and worth a backlog item before this carries real production
load.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import azure.functions as func

from api_blueprint import bp as api_bp

app = func.FunctionApp()
app.register_functions(api_bp)

REPO_ROOT = Path(__file__).resolve().parent.parent


@app.blob_trigger(arg_name="myblob", path="raw-resumes/{name}", connection="BLOB_CONNECTION_STRING")
def resume_blob_trigger(myblob: func.InputStream):
    logging.info(f"Blob trigger fired: {myblob.name} ({myblob.length} bytes)")

    if not myblob.name.lower().endswith(".pdf"):
        logging.info(f"Skipping non-PDF blob: {myblob.name}")
        return

    filename = os.path.basename(myblob.name)

    # ResumeParser.py reads from InputResume_Folder relative to its own cwd
    # — download the blob there so the existing script logic is unchanged.
    input_folder = REPO_ROOT / "InputResume_Folder"
    input_folder.mkdir(parents=True, exist_ok=True)
    local_path = input_folder / filename

    with open(local_path, "wb") as f:
        f.write(myblob.read())
    logging.info(f"Downloaded blob to {local_path}")

    try:
        result = subprocess.run(
            ["python", "ResumeParser.py"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            logging.error(f"ResumeParser.py failed for {filename}: {result.stderr[-2000:]}")
        else:
            logging.info(f"ResumeParser.py completed for {filename}")
            _apply_realtime_tier(filename)
    except subprocess.TimeoutExpired:
        logging.error(f"ResumeParser.py timed out (600s) for {filename}")


def _apply_realtime_tier(filename: str):
    """Real-time absolute-threshold tiering (realtime_tiering.py) for the
    one candidate ResumeParser.py just persisted -- NOT the same algorithm
    as ResumeMEtrics.py's batch relative-rank tiering (see that module's
    docstring for why a single-candidate real-time path can't reuse the
    batch percentile cutoffs). Fails soft: a Cosmos/import error here logs
    and returns rather than crashing the trigger -- ResumeParser.py's own
    write already succeeded, so the candidate record exists either way,
    just without a tier if this step fails."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "context-lake"))
    try:
        from candidate_id import candidate_id_for
        from realtime_tiering import compute_realtime_tier
        from repositories import get_candidate, upsert_candidate

        candidate_id = candidate_id_for(filename)
        candidate = get_candidate(candidate_id)
        if candidate is None:
            logging.warning(f"Real-time tiering skipped for {filename}: candidate {candidate_id} not found in Cosmos yet.")
            return
        candidate.update(compute_realtime_tier(candidate))
        upsert_candidate(candidate)
        logging.info(f"Real-time tier for {filename}: {candidate['ranking_tier']} (score={candidate['realtime_score']})")
    except Exception as e:
        logging.error(f"Real-time tiering failed for {filename}: {e}")
