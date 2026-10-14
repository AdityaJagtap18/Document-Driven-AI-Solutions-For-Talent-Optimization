---
id: interview_bot_answer_scoring
version: 1
source: interview_bot.py (run_interview_for_candidate)
model: gpt-3.5-turbo
temperature: 0.5
max_tokens: 1000
input_variables: [answers (JSON array of {question, answer} pairs typed by the candidate via input())]
output_format: JSON array requested in free text; written directly to score_summary.json with no json.loads/validation at all (not even a try/except around parsing — only the LLM call itself is wrapped)
---

```
You are a technical interviewer. Score the following candidate's answers out of 10, with brief justification for each.

Format:
[
  {
    "question": "...",
    "answer": "...",
    "score": ...,
    "feedback": "..."
  },
  ...
]

Candidate Responses:
{answers}
```

**Risk:** unlike `resume_merge_structured_extraction.v1`, this response is written straight to disk (`score_data = response.content`) with no `json.loads` at all — if the model doesn't return valid JSON, `score_summary.json` silently contains garbage and nothing downstream would catch it. Fix when cutting v2.

**Context:** `interview_bot.py` is a separate, CLI-driven interview flow (candidate types answers via `input()`) distinct from the Streamlit voicebot (`voicebot/voice_bot.py`) — they don't share code, only the same LLM pattern.
