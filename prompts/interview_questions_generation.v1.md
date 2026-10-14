---
id: interview_questions_generation
version: 1
source: generate_questions.py
model: gpt-3.5-turbo
temperature: 0.7
max_tokens: 800
input_variables: [resume_text]
output_format: numbered list (parsed by stripping leading digits/periods per line)
---

```
You are an AI interviewer.

Generate 10 relevant and intelligent interview questions based solely on the following candidate resume:

Resume Content:
"""{resume_text}"""

Return the questions in a clean numbered list format.
```
