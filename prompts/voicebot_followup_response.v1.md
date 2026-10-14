---
id: voicebot_followup_response
version: 1
source: voicebot/voice_bot.py (run_voice_interview)
model: gpt-3.5-turbo
temperature: 0.7
max_tokens: 1000
system_prompt: see voicebot_warmup_questions.v1.md (shared llm instance)
input_variables: [candidate_answer]
output_format: free text, spoken via TTS
fallback_on_error: "Thank you for your response!"
---

```
The candidate said: '{candidate_answer}'. Respond supportively as a warm interviewer.
```
