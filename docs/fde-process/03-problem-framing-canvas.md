# Problem Framing Canvas (FDE Stage 3)

## The problem

Manual resume screening for technical roles doesn't scale and isn't consistent reviewer-to-reviewer. CDPI already automated this with an LLM (GPT-3.5-turbo) before this engagement started, but that automation itself had a quieter, more specific problem: **the LLM's "JD Match %" was a self-report, not a computed measurement.** Nothing grounded that number in the actual resume text against the actual job description — the model was simply asked to estimate a percentage and state it with no verification step.

Verified this session: across all 42 real candidates in `resume_summary.csv`, the LLM's self-reported JD Match scores clustered overwhelmingly at 90% with almost no differentiation between genuinely strong and weak candidates. A computed semantic similarity score (cosine similarity between resume-chunk embeddings and the JD, see `semantic-library/`) on the same 42 candidates produced real spread (roughly 2.4–5.8 out of 10) — i.e., the LLM's number was telling recruiters almost nothing.

## Scope

End-to-end recruitment pipeline: resume intake → parsing/scoring → interview question generation → interview scheduling → optional AI voice-bot warm-up interview. This engagement's scope is migrating that pipeline to an Azure-native, secure, measurable architecture per the AI FDE spine — not redesigning the recruitment *process* itself (e.g., not changing who makes hiring decisions).

## Personas (confirmed this session)

Candidate, Recruiter/TA, Hiring Manager, Admin, Dev (internal maintainer). See `02-stakeholder-map.md` for how under-represented most of these are today.

## Success criteria

1. **Grounded, not guessed, matching** — done and verified (Semantic Match Score, Phase before this roadmap).
2. **No secrets in source or git history going forward** — done this session (9 found and fixed); historical git-history scrub still open.
3. **Safe to change** — automated tests + CI/CD (Phase 1).
4. **Production-operable on Azure** — context lake, event-driven pipeline, observability (Phases 2-3).
5. **Usable by non-developers** — frontend for the 5 personas (Phase 4).
6. **Measurable before/after value** — KPI baseline + variance analysis (Phase 0 / Phase 8).

## Non-functional requirements (current state, honestly assessed)

| NFR | Status before this engagement | Status now |
|---|---|---|
| Security | 9 hardcoded secrets in source/config/git | Fixed; git history scrub still open |
| Reliability | No retry/fallback on any of 8 LLM call sites | Still open (Phase 1) |
| Observability | `log.txt`, 20,000+ lines of freeform text; no metrics | Still open (Phase 3) |
| Cost awareness | No token/cost tracking anywhere | Still open (Phase 3) |
| Testability | Zero automated tests | Still open (Phase 1) |
| Explainability | JD Match % was an ungrounded LLM guess | Semantic Match Score now computed and verified (see above) |

## Out of scope (explicit)

- Hosting/deploying the frontend (Future Scope per developer instruction).
- External institution/board verification for document intelligence — internal consistency + tamper/forgery signals only, for now.
- Redesigning the underlying hiring *process* (who decides, how many interview rounds, etc.) — this is an infrastructure/AI-quality migration, not an HR process redesign.
