# Bias / Fairness Audit of Scoring Weights (FDE Stage 24 gap)

Reviewed `config/scoring_config.json` for disparate-impact risk. This is a findings-and-recommendations document — **no weights were changed as part of this audit**. Changing who ranks higher or lower is a product/ethics decision, not a technical default, so each recommendation below needs explicit sign-off before implementation (see the question at the end).

## Findings, ranked by severity

### 1. Employment Gaps Detected: flat -5 penalty, no reason distinction (High severity)

`penalty_score()`: `gap_penalty = -5 if gap else 0`. This is the single largest penalty value anywhere in the scoring system — larger than a full missing-certification or a red flag. It fires identically whether the gap was parental/caregiving leave, a medical issue, immigration delay, a sabbatical, or a layoff. This is a well-documented indirect bias vector: gap penalties disproportionately affect candidates who took parental or caregiving leave (predominantly women in most labor markets) and candidates with disabilities or chronic health conditions.

**Recommendation:** Either remove this penalty entirely, or reduce its magnitude significantly and route gap-detected candidates to human review instead of auto-penalizing (consistent with the Human Review Queue feature already in the backlog).

### 2. Culture Fit Score: LLM-subjective assessment used as a scoring input (High severity)

`culture_score()` reads an LLM-generated "High/Medium/Low" judgment (`Culture Fit Assessment` field) and converts it directly into score points (-0.25 to 3). "Culture fit" is one of the most extensively documented bias vectors in hiring research — historically used, even unintentionally, as a proxy for "resembles the existing team," which can systematically disadvantage candidates from different educational, cultural, or linguistic backgrounds. Compounding this: the judgment itself comes from an LLM whose "culture fit" reasoning has never been audited for what patterns it's actually keying on (e.g., resume phrasing style, which industries are mentioned, sentence structure that may correlate with native-English-speaker writing conventions).

**Recommendation:** Remove "Culture Fit Assessment" as a scoring input entirely (it can still be shown to a human reviewer as context, just not auto-scored), or at minimum require a documented, specific rubric for what "culture fit" means in this context before it's allowed to affect ranking.

### 3. Industry Penalty: -0.25 for any non-tech-labeled industry (Medium-High severity)

`industry_penalty()`: candidates get 0 penalty only if their industry text contains "tech/technology/it/cs/computer" — everyone else gets -0.25, regardless of how relevant their actual skills are. This penalizes career changers and candidates from adjacent fields (e.g., a finance analyst with strong Python/ML skills) even when the Skill Score and Project Score already capture their actual technical fit. Career-changers into tech skew toward specific demographics (often older workers and women relative to traditional CS-degree pipelines), so a flat non-tech penalty on top of already-present skill scoring risks double-penalizing the same signal.

**Recommendation:** Remove this penalty, or fold it into a more specific, skills-based signal (it's largely redundant with Skill Score and Project Score, which already measure actual technical relevance better than an industry-label heuristic).

### 4. Effort Needed by the Company: rewards "low effort" (Medium severity)

`effort_score()` gives the highest score (3) to "Low" effort and penalizes "High" effort (-0.25). This frames a candidate's need for onboarding support, accommodations, or ramp-up time as a negative trait in itself, rather than a neutral operational fact. This can disadvantage candidates re-entering the workforce, career changers, or candidates needing accessibility accommodations — all of whom may be fully capable but require more initial support.

**Recommendation:** Remove this as a scored dimension, or reframe it as purely informational for the hiring manager (shown in the dashboard, not fed into ranking).

### 5. Candidate Type Score: Junior systematically scores lowest (Lower severity, structural)

`candidate_type_score()`: junior=1.5, mid-level=4, senior=5 — junior candidates score roughly a third of what senior candidates do, compounded with Experience Score's similar structure. This is defensible for genuinely experience-dependent roles, but worth naming explicitly: this scoring system structurally favors candidates with more years of experience, which correlates with age, and will systematically disadvantage younger/early-career candidates and career changers regardless of actual skill level on the Skill/Project Score dimensions.

**Recommendation:** No change needed if this project role genuinely requires seniority — but this should be a conscious choice per job posting, not a fixed default applied to every JD.

## What's NOT flagged (reviewed, no significant concern found)

- **JD Match Score / Semantic Match Score**: computed from actual text similarity, not demographic-correlated proxies. Embeddings can still encode subtle biases toward "standard" corporate phrasing (worth monitoring, not a confirmed finding here — no evidence gathered either way).
- **Skill Score, Project Score, Certificate Score, Contribute Score, Soft Skill Score**: directly measure stated qualifications, not identity-correlated proxies, at least on their face. Not deeply audited — scope was focused on the weights clearly flagged above.

## Decision needed

This audit stops at findings — implementing any of the above (removing/reweighting Culture Fit, Effort, Industry Penalty, Employment Gaps penalty) changes real candidate rankings and should be the user's call, not an automatic follow-up.
