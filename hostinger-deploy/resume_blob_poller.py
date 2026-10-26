"""Hostinger Cron Job replacement for function_app.py's resume_blob_trigger
(a real Azure Blob Storage event trigger -- Hostinger shared hosting has
no equivalent event mechanism, only scheduled Cron Jobs).

Same real work the Azure blob trigger does, just on a poll instead of an
event: list the raw-resumes container, find blobs not yet processed
(tracked locally in PROCESSED_LOG, since there's no event to mark "new"),
download each, run the same real ResumeParser.py pipeline via the same
subprocess call function_app.py's trigger makes, then apply the same
real-time tier. Processing is not instant like the Azure version -- it
runs on whatever interval the Cron Job is set to (a real, stated
trade-off, not hidden).

Run manually to verify before scheduling:
    python hostinger-deploy/resume_blob_poller.py

Hostinger Cron Job command (hPanel -> Advanced -> Cron Jobs), e.g. every
5 minutes:
    */5 * * * * /home/<user>/<app-dir>/venv/bin/python /home/<user>/<app-dir>/hostinger-deploy/resume_blob_poller.py >> /home/<user>/<app-dir>/logs/resume_poller.log 2>&1
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (_REPO_ROOT, _REPO_ROOT / "context-lake"):
    sys.path.insert(0, str(_p))

# Reuses function_app.py's own real trigger-body logic unchanged (download
# + subprocess + real-time tiering) rather than re-implementing it --
# only the "how do I find new blobs" part differs (poll vs. event).
sys.path.insert(0, str(_REPO_ROOT / "azure-functions"))
from function_app import _apply_realtime_tier  # noqa: E402

import client as context_lake_client  # noqa: E402

PROCESSED_LOG = Path(__file__).resolve().parent / "resume_poller_processed.txt"


def _already_processed() -> set:
    if not PROCESSED_LOG.exists():
        return set()
    return set(PROCESSED_LOG.read_text(encoding="utf-8").splitlines())


def _mark_processed(name: str):
    with open(PROCESSED_LOG, "a", encoding="utf-8") as f:
        f.write(name + "\n")


def run_once():
    import subprocess

    container = context_lake_client.get_blob_container("raw-resumes")
    processed = _already_processed()

    input_folder = _REPO_ROOT / "InputResume_Folder"
    input_folder.mkdir(parents=True, exist_ok=True)

    new_blobs = [b for b in container.list_blobs() if b.name.lower().endswith(".pdf") and b.name not in processed]
    if not new_blobs:
        logging.info("No new resumes in raw-resumes.")
        return

    for blob in new_blobs:
        filename = Path(blob.name).name
        local_path = input_folder / filename
        logging.info(f"Downloading new blob: {blob.name}")
        with open(local_path, "wb") as f:
            f.write(container.download_blob(blob.name).readall())
        _mark_processed(blob.name)

    try:
        result = subprocess.run(
            ["python", "ResumeParser.py"], cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            logging.error(f"ResumeParser.py failed: {result.stderr[-2000:]}")
            return
        logging.info("ResumeParser.py completed.")
    except subprocess.TimeoutExpired:
        logging.error("ResumeParser.py timed out (600s).")
        return

    for blob in new_blobs:
        _apply_realtime_tier(Path(blob.name).name)


if __name__ == "__main__":
    run_once()
