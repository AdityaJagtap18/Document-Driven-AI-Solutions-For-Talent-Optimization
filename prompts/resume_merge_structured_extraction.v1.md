---
id: resume_merge_structured_extraction
version: 1
source: ResumeParser.py (summarize_pdf, merge step)
model: gpt-3.5-turbo
temperature: 0.3
max_tokens: 1500
input_variables: [summaries (joined chunk outputs), jd]
output_format: JSON (requested in free text, parsed with bare json.loads, no retry/repair)
---

```
You are an expert resume evaluator.

Below are summaries of different chunks of a resume, all based on the same candidate:

"""{summaries}"""

Job Description:
"""{jd}"""

Now based on the summaries and the JD, extract the following structured JSON.

Return clean JSON output with this format:
{
    "Name": "<Extracted full name of the candidate>",
    "JD Match": "<% Match>",
    "Missing Keywords": {
        "Technical Skills": [],
        "Tools & Technologies": [],
        "Concepts & Methodologies": []
    },
    "Top Matching Keywords": [],
    "Profile Summary": "<Brief summary related to JD>",
    "Projects": [
        {
            "Project Name": "<Title>",
            "Relevance to JD": "<High/Medium/Low>",
            "Technologies Used": [],
            "Impact": "<Project outcomes>"
        }
    ],
    "Certifications & Courses": ["<Relevant Certifications>"],
    "Skills That Will Contribute to the Company": [],
    "Soft Skills & Leadership Qualities": ["<Communication, Leadership, etc.>"],
    "Industry Experience": "<Relevant industries like Finance, Healthcare>",
    "Culture Fit Assessment": "<High/Medium/Low – Explanation>",
    "Potential Concerns": ["<Gaps, missing skills, weaknesses>"],
    "Red Flags & Risk Analysis": ["<Major issues>"],
    "Candidate's Growth Potential": "<How much they can grow in the company>",
    "Effort Needed by the Company": "<Low/Medium/High – Explanation>",
    "Resume Strength Score": "<Numeric score between 0.0 and 10.0>",
    "Relevant Experience (yrs)": "<Years of directly relevant experience>",
    "Employment Gaps Detected": true,
    "Resume Format Quality": "<Good/Average/Poor>",
    "Candidate Type": "<Junior/Mid-Level/Senior>",
    "HR Notes": "<Any special observations for HR>"
}

Return only the JSON.
```

**Post-processing (not part of the prompt):** `Email`, `Phone`, `LinkedIn` are regex-extracted from the raw resume text (not by the LLM) and merged into this JSON afterward, along with `resume_name`.
