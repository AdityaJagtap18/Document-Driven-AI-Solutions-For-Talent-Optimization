---
id: voicebot_followup_no_response
version: 1
source: voicebot/voice_bot.py (run_voice_interview)
model: gpt-3.5-turbo
temperature: 0.7
max_tokens: 1000
system_prompt: see voicebot_warmup_questions.v1.md (shared llm instance)
input_variables: [question]
output_format: free text, spoken via TTS
fallback_on_error: "It's okay, take your time. Let's move to the next one."
---

```
The candidate did not respond to: '{question}'. Say something gentle and supportive.
```
