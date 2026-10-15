# Before-AI KPI Baseline (FDE Stage 4)

A literal "before this project existed" baseline isn't recoverable retroactively — CDPI's AI-assisted screening already existed before this engagement started. What follows is honest about that: it baselines the state **at the start of this engagement** (an already-AI-assisted but unverified/insecure system) against measurements taken **after** the work done so far, and sets the pattern for baselining each future phase at its own start, per the master roadmap (`docs/architecture.md` links the roadmap context; the phase plan itself lives in the tracker's `Feature_Backlog` sheet).

## Baseline measured this session (real data, not estimated)

| KPI | Before this engagement | After (measured) | Evidence |
|---|---|---|---|
| JD-match score trustworthiness | LLM self-report, clustered ~90% for nearly all 42 candidates — almost no differentiation | Computed Semantic Match Score, real spread ~2.4–5.8/10 across the same 42 candidates | `CandidateMetrics_Folder/candidate_ranking_metrics.csv`, commit `1139896` |
| Resume chunk retrieval quality | Naive char-based chunking, top similarity 0.39–0.49, top matches mostly unrelated to the JD's actual skill | Section-aware chunking, top similarity 0.55–0.60, top matches literally contain the JD's key skill terms | `semantic-library/README.md` "Verified against real data", commit `58b5255` |
| Hardcoded secrets in source/config | 9 (OpenAI key ×4 locations, Gmail app password ×2, ElevenLabs key ×2, AssemblyAI key, MySQL root password) | 0 (all moved to `.env`/gitignored config; git history still has them — rotation recommended) | `Security_Remediation_Log` tracker sheet, commits `ca6e665`..`cadcc72` |
| Documented/versioned LLM prompts | 0 — all 8 prompts were inline f-strings with no version history | 8, versioned with model/temperature/known-issues metadata | `/prompts/registry.json` |
| Automated tests | 0 | 0 — **Phase 1, not yet started** | — |
| Candidate data loss risk | `resume_summary.csv` silently overwritten on every parser run — confirmed 48 of 84 real candidates were already missing from the live CSV before this session, recoverable only via archived snapshots | Backfill script now recovers from archives automatically; context lake (Cosmos DB) would eliminate this failure mode entirely once provisioned (Phase 2) | `context-lake/README.md` "Backfill findings" |

## Baseline protocol for future phases

Each phase in the roadmap that ships a feature with no prior version (frontend, WhatsApp notifications, document intelligence) must capture a short "before" snapshot at phase start — e.g., for document intelligence: "candidates can type any claimed credential with zero verification today" — before building the fix, the same way this document captured the resume-matching and secrets baselines above. This keeps `KPI_Tracker` honest instead of back-filled after the fact.

## What's explicitly NOT measured yet (open gaps, not hidden)

- Cycle time (resume upload → ranked tier): no timestamps captured anywhere today.
- Manual review effort: no tracking of how long a human spends on "To be Considered" tier candidates.
- Cost per candidate: no token/cost tracking exists (Phase 3 closes this).
- Hire outcomes: no feedback loop from actual hiring decisions back into the scoring model.

These remain open in `KPI_Tracker` as "Not yet measured" rather than invented placeholder numbers.
