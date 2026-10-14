---
id: resume_chunk_summarization
version: 1
source: ResumeParser.py (summarize_pdf, chunk loop)
model: gpt-3.5-turbo
temperature: 0.7
max_tokens: 1000
input_variables: [text, jd]
output_format: free text (bullet/JSON-ish, not enforced)
---

```
You are an AI assistant. Summarize the relevant resume details below in relation to the following job description.

Resume Chunk:
"""{text}"""

Job Description:
"""{jd}"""

Extract the key points in concise bullet form, JSON-style if possible. Do NOT return full JSON yet.
```
