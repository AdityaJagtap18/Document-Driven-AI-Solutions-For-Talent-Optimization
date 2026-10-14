---
id: voicebot_transcript_scoring
version: 1
source: voicebot/voice_bot.py (run_voice_interview)
model: gpt-3.5-turbo
temperature: 0.7
max_tokens: 1000
system_prompt: see voicebot_warmup_questions.v1.md (shared llm instance)
input_variables: [job_description, qa_pairs (Q/A transcript block)]
output_format: free text; first number matched via regex \b(\d+(\.\d+)?)\b is taken as the score
---

```
Job Description:
{job_description}

Evaluate the candidate's answers below based on relevance, clarity, and enthusiasm. Return an overall score out of 10.

Q1: {question_1}
A1: {answer_1}
Q2: {question_2}
A2: {answer_2}
...
```

**Risk:** the score is extracted via `re.search(r"\b(\d+(\.\d+)?)\b", score_text)` — if the model mentions any other number first (e.g. "after 3 questions, I'd say..."), the wrong value is captured. Worth fixing with structured output (JSON mode) when cutting v2.
