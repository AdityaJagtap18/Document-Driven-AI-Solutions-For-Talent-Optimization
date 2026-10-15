---
id: interview_bot_answer_scoring
version: 2
source: interview_bot.py (run_interview_for_candidate)
model: gpt-3.5-turbo
temperature: 0.5
max_tokens: 1000
input_variables: [answers (JSON array of {question, answer} pairs typed by the candidate via input())]
output_format: JSON object with a "scores" key, enforced via OpenAI response_format={"type":"json_object"} — the array is unwrapped (parsed["scores"]) before being written to score_summary.json, so the file on disk keeps the same array shape downstream consumers (context-lake/migrate_from_csv.py) already expect.
changed_from_v1: v1 asked for a bare top-level JSON array, which OpenAI's JSON mode rejects (response_format={"type":"json_object"} requires the top level to be an object). Rewrote the prompt to request {"scores": [...]} instead. This also fixes the v1 risk note — the response is now actually json.loads()-validated before being written to disk, instead of writing the raw LLM text with zero validation.
---

```
You are a technical interviewer. Score the following candidate's answers out of 10, with brief justification for each.

Return a JSON object with this exact shape:
{
  "scores": [
    {
      "question": "...",
      "answer": "...",
      "score": ...,
      "feedback": "..."
    },
    ...
  ]
}

Candidate Responses:
{answers}
```

**Verified for real** (Phase 1, this session): tested the exact prompt+response_format combination against the live API — model correctly wraps the array under "scores" as instructed, `json.loads()` succeeds, `parsed["scores"]` extracts the array cleanly.
