# Stakeholder Map (FDE Stage 2)

| Role | Who today | Decision rights | Gap |
|---|---|---|---|
| Business / Product owner | Aditya Jagtap | Full — only decision-maker on the project | Single point of failure (see Stage 37 note in the tracker) |
| Engineering owner | Aditya Jagtap (+ this Claude Code engagement) | Full | Same as above |
| Talent Acquisition | Represented only as an email recipient (`config/email_templates/talent_acquisition.json`) | None — receives reports, doesn't set requirements | No named person; requirements for "what a good candidate report looks like" were inferred from the template, not gathered from a real TA stakeholder |
| Hiring Manager | Represented only as an email recipient (`business_lead.json`) | None today | Same gap |
| Technical Evaluator | Represented only as an email recipient (`technical_evaluation.json`) | None today | Same gap |
| Candidate (end user) | Applies via resume drop/email today; will get a self-service portal in Phase 4 | None — pure data subject today | No feedback channel exists; candidates can't see their own status or dispute a score |
| Security / Compliance | Nobody | None | This session (Claude Code) acted as the de facto security reviewer, finding and fixing 9 hardcoded secrets — but there's no ongoing owner once this engagement ends |
| Executive / Governance | Nobody | None | No one signs off on scoring-weight changes, model swaps, or data retention |

## Why this matters for the roadmap

Several backlog items exist specifically because no real stakeholder was ever consulted on them: the scoring weights in `config/scoring_config.json` were never reviewed for bias (flagged as Phase 1's "Bias/Fairness Audit of Scoring Weights"), and the document-intelligence certificate-validation scope (internal consistency + tamper signals, no external institution verification) was decided by the developer alone, not a compliance stakeholder. Phase 7 (Governance/TEVV/Production-Readiness) is where this gap should start closing — at minimum, a documented decision log standing in for stakeholder sign-off until real stakeholders exist.
