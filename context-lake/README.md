# Context Lake

CDPI's current "context" is scattered across local folders and flat files: `InputResume_Folder`, `OutputResume_Folder`, `Resume_Parsed_CSVs/resume_summary.csv`, 100+ per-candidate `InterviewData/<Name>/interview_questions.json` folders, `log.txt` (freeform text), and `CandidateMetrics_Folder/candidate_ranking_metrics.csv`. Nothing is queryable as a whole, there's no single source of truth for "everything known about candidate X," and the dashboard (see `Feature_Backlog` in the Excel tracker) has nothing to read from.

This folder defines the target centralized data layer — Stage 11 of the FDE spine ("Data, Knowledge & Context Strategy").

## Target architecture

| Today (brownfield) | Target (context lake) |
|---|---|
| `InputResume_Folder/`, `OutputResume_Folder/` (local disk) | **Azure Blob Storage** — containers `raw-resumes`, `parsed-resumes` (archival, source of truth for re-processing) |
| `Resume_Parsed_CSVs/resume_summary.csv` | **Azure Cosmos DB (NoSQL API)** — container `candidates`, one document per candidate (see `schema/candidate.schema.json`) |
| `InterviewData/<Name>/interview_questions.json`, voicebot `questions.json`/`answers.json` | Cosmos DB container `interview_transcripts` (see `schema/interview_transcript.schema.json`) |
| `CandidateMetrics_Folder/candidate_ranking_metrics.csv` | Cosmos DB container `scoring_history` — append-only, one record per scoring run (preserves before/after comparisons instead of overwriting the CSV) |
| `log.txt` (freeform text) | **Azure Monitor / Application Insights** — structured traces, queryable via Kusto (KQL), feeds the observability dashboards |
| *(nothing)* | `audit_log` container — immutable append-only record of every AI decision (score, JD match, ranking tier) with inputs/outputs/model/version, satisfying FDE Stage 25 "Audit Coverage" |

## Why this matters beyond tidiness

1. **The dashboard feature has nowhere to read from today** — a frontend can't show live candidate rankings or pipeline funnel stats from five different CSV/JSON file trees. Cosmos DB + Blob gives it one queryable backend.
2. **The semantic library needs a feed** — [`../semantic-library/`](../semantic-library/README.md)'s embedding pipeline should read from `candidates` in Cosmos DB, not re-parse CSVs.
3. **Audit coverage is currently zero** — `log.txt` has the data buried in it but it's not queryable evidence; a regulator or exec asking "why was this candidate rejected" can't be answered without grepping a 20,000-line text file.

## Schemas

- [`schema/candidate.schema.json`](schema/candidate.schema.json) — the structured output already produced by `resume_merge_structured_extraction.v1` (see `../prompts/`), extended with lake-only fields (`_id`, `ingested_at`, `source_blob_path`, `ranking_tier`).
- [`schema/interview_transcript.schema.json`](schema/interview_transcript.schema.json) — merges all three interview flows into one record shape: `generate_questions.py` (questions only), `interview_bot.py` (CLI Q&A with per-question LLM scoring), and the voicebot (`questions.json`/`answers.json`/`score.json`).

## Code

- [`client.py`](client.py) — Cosmos DB + Blob Storage client factories, reading `COSMOS_ENDPOINT`/`COSMOS_KEY`/`COSMOS_DATABASE`/`BLOB_CONNECTION_STRING` from `.env` (see `../.env.example`). Raises a clear `RuntimeError` naming the missing variable(s) instead of a bare SDK error if the resources aren't configured yet. Auto-creates the database/containers/blob containers on first use — nothing to provision by hand beyond the Cosmos account and Storage account themselves.
- [`repositories.py`](repositories.py) — CRUD functions for all four containers: `upsert_candidate`/`get_candidate`/`find_candidate_by_resume_name`/`list_candidates`, `upsert_interview_transcript`/`list_interview_transcripts`, `append_scoring_history`/`list_scoring_history` (append-only), `append_audit_log`/`list_audit_log` (append-only, immutable).
- [`blob_store.py`](blob_store.py) — `upload_resume_blob`/`download_resume_blob`/`blob_exists` for the `raw-resumes`/`parsed-resumes` containers.
- [`migrate_from_csv.py`](migrate_from_csv.py) — one-time backfill script, run with `--dry-run` first (writes a preview JSON, makes no Azure connection — safe to run with no resources provisioned at all).
- [`candidate_id.py`](candidate_id.py) — `candidate_id_for(resume_name)`, a deterministic uuid5 shared by `migrate_from_csv.py` and `ResumeParser.py`'s live dual-write, so the same resume always maps to the same Cosmos document regardless of which path writes it first.

Local imports in this folder are flat (`import client`, not `from context_lake.client import`) — the directory name has a hyphen, which isn't a valid Python package identifier, same convention `voicebot/` already uses for its own local imports.

Install this folder's own dependencies with `pip install -r context-lake/requirements.txt` (kept separate from the root `requirements.txt` since nothing else in the repo imports this code yet).

## Backfill findings (from running `migrate_from_csv.py --dry-run` against this repo's real data)

Running the dry-run surfaced a genuine brownfield data-loss bug, not a script bug: `Resume_Parsed_CSVs/resume_summary.csv` gets **overwritten** on every `ResumeParser.py` run, so candidates processed in earlier runs silently disappear from it. 48 of this repo's 84 known candidates were missing from the live CSV entirely — recoverable only because `InviteSender/Invitations_Archive/resume_summary_*.csv` happened to keep timestamped snapshots. The script now also reads those archives and merges in anything missing from the live file (oldest archive first, so the newest available snapshot wins per candidate).

After that recovery, 84 candidate documents and 85 interview transcripts were built from this repo's actual data, with only 4 transcripts left unmatched to a candidate:
- `Buddha_Vamsi` and `Kamballa_Pradeep` — their `OutputResume_Folder` filenames have a double underscore (`Buddha_Vamsi__resume.pdf`), so the folder-name-based `candidate_id` guess doesn't match `resume_name` in the CSV. Needs a manual fix if these candidates matter, or renaming the source PDF.
- Two `voicebot/InterviewData/` entries (`Aditya_Jagtap`, `Rajendra_Jagtap`) — these are the developer's own test interviews, not real candidates, so having no matching resume is expected and correct.

This is exactly the kind of silent data loss the context lake exists to prevent going forward — once writes go through `repositories.py` instead of overwriting a CSV, this class of bug can't recur.

## Status

Data-access layer (`client.py`, `repositories.py`, `blob_store.py`) and the backfill script (`migrate_from_csv.py`) are built and dry-run verified against this repo's real data (see above).

**Now wired into the live pipeline** (Phase 2 of the roadmap): `ResumeParser.py` dual-writes a candidate document to Cosmos DB after every resume it parses, and `ResumeMEtrics.py` appends a `scoring_history` record per candidate after every scoring run — both alongside the existing CSV writes, which stay the authoritative output for now. Both writes are wrapped in their own `try/except`: if Cosmos DB isn't configured, the attempt fails, logs a clear reason (`write_log()` in ResumeParser.py, a `[WARN]` print in ResumeMEtrics.py), and the script continues exactly as before. Verified for real: ran both scripts end-to-end with no Cosmos DB configured, confirmed exit code 0, correct CSV output, and the expected fail-soft log message naming the missing `COSMOS_ENDPOINT`/`COSMOS_KEY`/`COSMOS_DATABASE`.

**No Azure Cosmos DB / Storage account is provisioned yet** — see [`../docs/azure-provisioning/phase2-cosmos-storage-keyvault.md`](../docs/azure-provisioning/phase2-cosmos-storage-keyvault.md) for step-by-step setup (Portal or Bicep). Once provisioned and `.env` is filled in, the dual-write above starts landing data automatically — no further code changes needed. Then run `migrate_from_csv.py` (without `--dry-run`) once to backfill the historical 84 candidates / 85 transcripts that predate the live wiring.
