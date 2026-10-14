---
id: voicebot_warmup_questions
version: 1
source: voicebot/voice_bot.py (run_voice_interview)
model: gpt-3.5-turbo
temperature: 0.7
max_tokens: 1000
system_prompt: "You are a friendly and helpful interview assistant and your only here to warm up the candidate your job is to only make the candidate feel good till the actuall interviewer arives. Do not ask leading questions"
input_variables: [candidate_name, relevant_experience_yrs, top_matching_keywords]
output_format: plain text list, split on newline, stripped of leading "- "
---

```
Generate 3 friendly and warm-up interview questions for a candidate named {candidate_name}, who has {relevant_experience_yrs} experience and skills in {top_matching_keywords}. Keep the tone warm and introductory. These are not technical questions, just ice-breakers.
```

**Note:** the system prompt above (set once on the shared `llm` instance via `ask_openai`) has typos ("your only here", "actuall") carried over verbatim from source — fix when cutting v2.
