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
| [resume_merge_structured_extraction.v1.md](resume_merge_structured_extraction.v1.md) | `ResumeParser.py` | Merge chunk summaries into the final structured candidate JSON |
| [interview_questions_generation.v1.md](interview_questions_generation.v1.md) | `generate_questions.py` | Generate 10 interview questions from a resume |
| [voicebot_warmup_questions.v1.md](voicebot_warmup_questions.v1.md) | `voicebot/voice_bot.py` | Generate 3 ice-breaker questions |
| [voicebot_followup_response.v1.md](voicebot_followup_response.v1.md) | `voicebot/voice_bot.py` | Supportive reply after a candidate answer |
| [voicebot_followup_no_response.v1.md](voicebot_followup_no_response.v1.md) | `voicebot/voice_bot.py` | Gentle reply when the candidate doesn't answer |
| [voicebot_transcript_scoring.v1.md](voicebot_transcript_scoring.v1.md) | `voicebot/voice_bot.py` | Score the full warm-up transcript out of 10 |
| [interview_bot_answer_scoring.v1.md](interview_bot_answer_scoring.v1.md) | `interview_bot.py` | Score a candidate's typed Q&A answers out of 10 (separate CLI flow from the voicebot) |

## Known issues to fix when versioning forward (tracked in the Excel tracker, `Feature_Backlog` sheet)

- None of these prompts are schema-constrained (no JSON mode / function calling / Pydantic-structured output) — `resume_merge_structured_extraction.v1` asks for "clean JSON" in free text and the caller does a bare `json.loads` with no retry on failure; `interview_bot_answer_scoring.v1` is worse — its response is written straight to disk with no `json.loads` validation at all.
- No prompt-injection guarding: raw resume text and candidate speech are interpolated directly into prompts with no delimiter-escaping or instruction-hierarchy reinforcement.
- No few-shot examples or golden-case eval set exists yet for any of these (TEVV Stage 26 gap).
