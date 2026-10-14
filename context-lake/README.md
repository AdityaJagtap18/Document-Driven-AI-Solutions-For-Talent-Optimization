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
- [`schema/interview_transcript.schema.json`](schema/interview_transcript.schema.json) — merges the voicebot's `questions.json` + `answers.json` + score into one record per interview.

## Status

Design only, no migration implemented yet. Migration order is tracked in the Excel tracker (`FDE_Stage_Tracker` sheet, Stage 11 and Stage 14 rows): Blob containers first (lowest risk, pure lift-and-shift of file storage), then Cosmos DB for `candidates` (requires writing a one-time CSV→Cosmos backfill script), then `audit_log` (net-new, no backfill possible — starts clean from cutover date).
