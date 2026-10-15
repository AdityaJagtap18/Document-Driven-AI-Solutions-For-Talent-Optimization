# Prompt Library

Every LLM prompt used anywhere in CDPI, extracted verbatim from source and versioned here. This is Stage 19 ("Build Intelligence Core") evidence per the AI FDE spine — prompts should be explicit, reviewable, and traceable to the code that calls them, not buried inline in scripts.

## Convention

- One file per prompt, named `<use-case>.v<N>.md`.
- Each file has a front-matter block (model, temperature, max_tokens, input variables, source file/line) followed by the exact template text.
- `registry.json` is the machine-readable index — update it whenever a prompt file is added or a new version is cut.
- Bump the version suffix (`v1` → `v2`) on any change to the template text, variables, or target schema; never edit a shipped version in place, so past runs stay reproducible/auditable (TEVV requires this).

## Current prompts

| File | Used by | Purpose |
|---|---|---|
| [resume_chunk_summarization.v1.md](resume_chunk_summarization.v1.md) | `ResumeParser.py` | Summarize one resume chunk against the JD |
| [resume_merge_structured_extraction.v2.md](resume_merge_structured_extraction.v2.md) | `ResumeParser.py` | Merge chunk summaries into the final structured candidate JSON (JSON mode enforced — see v1 for the pre-Phase-1 free-text version) |
| [interview_questions_generation.v1.md](interview_questions_generation.v1.md) | `generate_questions.py` | Generate 10 interview questions from a resume |
| [voicebot_warmup_questions.v1.md](voicebot_warmup_questions.v1.md) | `voicebot/voice_bot.py` | Generate 3 ice-breaker questions |
| [voicebot_followup_response.v1.md](voicebot_followup_response.v1.md) | `voicebot/voice_bot.py` | Supportive reply after a candidate answer |
| [voicebot_followup_no_response.v1.md](voicebot_followup_no_response.v1.md) | `voicebot/voice_bot.py` | Gentle reply when the candidate doesn't answer |
| [voicebot_transcript_scoring.v1.md](voicebot_transcript_scoring.v1.md) | `voicebot/voice_bot.py` | Score the full warm-up transcript out of 10 |
| [interview_bot_answer_scoring.v2.md](interview_bot_answer_scoring.v2.md) | `interview_bot.py` | Score a candidate's typed Q&A answers out of 10 (JSON mode enforced, wrapper key added — see v1 for the pre-Phase-1 bare-array version) |

`resume_merge_structured_extraction.v1.md` and `interview_bot_answer_scoring.v1.md` are kept for history (`status: "superseded"` in `registry.json`) — never edit a shipped version, per the convention above.

## Known issues to fix when versioning forward (tracked in the Excel tracker, `Feature_Backlog` sheet)

- `resume_chunk_summarization.v1` and the three `voicebot_*` prompts are still free-text/unvalidated — only the two merge/scoring prompts have been moved to JSON mode so far (Phase 1 of the roadmap). The chunk-summarization prompt's output is intermediate (feeds the merge prompt, never parsed as JSON itself) so it's lower priority; the voicebot prompts are conversational text by design, not structured data, so JSON mode doesn't apply to them.
- No prompt-injection guarding: raw resume text and candidate speech are interpolated directly into prompts with no delimiter-escaping or instruction-hierarchy reinforcement.
- No few-shot examples or golden-case eval set exists yet for any of these (TEVV Stage 26 gap).
- No retry/backoff on any of the 8 LLM call sites yet — next item in Phase 1.
