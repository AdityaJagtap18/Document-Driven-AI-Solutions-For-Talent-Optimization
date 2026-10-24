# Prompt Library

Every LLM prompt used anywhere in CDPI's production code paths, extracted verbatim from source and versioned here. This is Stage 19 ("Build Intelligence Core") evidence per the AI FDE spine — prompts should be explicit, reviewable, and traceable to the code that calls them, not buried inline in scripts. Verified against the repository by grepping every real `ChatOpenAI(...)`/`call_with_retry(...)` call site — not assumed from file names or doc comments.

**Scope note:** this library covers LLM *prompts* only — text sent to a chat/completion model. It does not cover the Communications Configuration Console's *message templates* (candidate invite emails, etc. — see `comms_config.py` / `/admin/communications-config`), which are a different kind of template (recipient-facing text with placeholders, never sent to an LLM) managed by a separate system. The repo-root `*.ipynb` notebooks (`Code1.ipynb`, `ResumeMEtrics.ipynb`, `ResumeParser.ipynb`, `Untitled.ipynb`, `backup/ResumeParser.ipynb`) are excluded as non-production scratch/exploration artifacts, not the scripts CDPI actually runs.

## Convention

- One file per prompt, named `<use-case>.v<N>.md`.
- Each file has a front-matter block (model, temperature, max_tokens, input variables, source file/line) followed by the exact template text.
- `registry.json` is the machine-readable index — update it whenever a prompt file is added or a new version is cut.
- Bump the version suffix (`v1` → `v2`) on any change to the template text, variables, or target schema; never edit a shipped version in place, so past runs stay reproducible/auditable (TEVV requires this).

## Current prompts

| File | Used by | Purpose |
|---|---|---|
| [resume_chunk_summarization.v1.md](resume_chunk_summarization.v1.md) | `ResumeParser.py` (via `rag_config.py`) | Summarize one resume chunk against the JD |
| [resume_merge_structured_extraction.v2.md](resume_merge_structured_extraction.v2.md) | `ResumeParser.py` (via `rag_config.py`) | Merge chunk summaries into the final structured candidate JSON (JSON mode enforced — see v1 for the pre-Phase-1 free-text version) |
| [interview_questions_generation.v1.md](interview_questions_generation.v1.md) | `generate_questions.py` | Generate 10 interview questions from a resume |
| [voicebot_warmup_questions.v1.md](voicebot_warmup_questions.v1.md) | `voicebot/voice_bot.py` | Generate 3 ice-breaker questions |
| [voicebot_followup_response.v1.md](voicebot_followup_response.v1.md) | `voicebot/voice_bot.py` | Supportive reply after a candidate answer |
| [voicebot_followup_no_response.v1.md](voicebot_followup_no_response.v1.md) | `voicebot/voice_bot.py` | Gentle reply when the candidate doesn't answer |
| [voicebot_transcript_scoring.v1.md](voicebot_transcript_scoring.v1.md) | `voicebot/voice_bot.py` | Score the full warm-up transcript out of 10 |
| [interview_bot_answer_scoring.v2.md](interview_bot_answer_scoring.v2.md) | `interview_bot.py` | Score a candidate's typed Q&A answers out of 10 (JSON mode enforced, wrapper key added — see v1 for the pre-Phase-1 bare-array version) |

`resume_merge_structured_extraction.v1.md` and `interview_bot_answer_scoring.v1.md` are kept for history (`status: "superseded"` in `registry.json`) — never edit a shipped version, per the convention above.

### These two files are no longer the single source of truth for what runs

`resume_chunk_summarization` and `resume_merge_structured_extraction` are the only two prompts with a **second, real, runtime representation**: `ResumeParser.py` no longer hardcodes either one — it loads whichever version is currently **active** from `rag_config.py`'s versioned Cosmos store (`rag_prompt_versions`), administered via the RAG Configuration Console's Prompts tab (`/admin/rag-config`). The `.md` files here document the **default/seed** text (byte-for-byte what `ResumeParser.py` used to hardcode, and what it falls back to if nothing's ever been activated) — if an admin has drafted and activated different wording through that console, the live prompt differs from what's below. Check `/admin/rag-config`'s Prompts tab for what's actually active before assuming this file is current. The other six prompts (`interview_questions_generation`, the four `voicebot_*` ones, `interview_bot_answer_scoring`) have no such runtime versioning — their `.md` file here *is* the live prompt, verbatim.

## Known issues to fix when versioning forward (tracked in the Excel tracker, `Feature_Backlog` sheet)

- `resume_chunk_summarization.v1` and the three `voicebot_*` prompts are still free-text/unvalidated — only the two merge/scoring prompts have been moved to JSON mode so far (Phase 1 of the roadmap). The chunk-summarization prompt's output is intermediate (feeds the merge prompt, never parsed as JSON itself) so it's lower priority; the voicebot prompts are conversational text by design, not structured data, so JSON mode doesn't apply to them.
- **Prompt-injection guarding is now partial, not absent.** `resume_chunk_summarization` and `resume_merge_structured_extraction` sanitize their untrusted inputs (resume chunk text, the JD, and the LLM-produced chunk summaries fed into the merge step) via `rag_config.sanitize_for_prompt()` before interpolation — it neutralizes triple-quote delimiter breakout and flags common injection phrasing inline (see `rag_config.py`'s module docstring). The other six prompts still interpolate raw, unsanitized input directly: `interview_questions_generation`'s resume text, and all four `voicebot_*` prompts' candidate speech/name, have no equivalent guard yet.
- No few-shot examples or golden-case eval set exists yet for any of these (TEVV Stage 26 gap).

All 8 call sites retry with exponential backoff on failure (`llm_utils.call_with_retry`, Phase 1 of the roadmap).
