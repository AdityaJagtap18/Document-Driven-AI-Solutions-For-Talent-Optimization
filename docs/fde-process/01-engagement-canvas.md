# Engagement Canvas (FDE Stage 1)

## Client context

CDPI (Cognitive Document Intelligence Platform) started as a B.E. Final Year Project (2024-25) at DY Patil College of Engineering, Charoli, built by Group 3. The `cognitivedocumentintelligecepl@gmail.com` address and "CIDP" branding in email templates suggest ambition toward a real product, not just coursework — but no company entity, funding, or external client exists today. This canvas reflects the project honestly as a solo-developer-owned system being hardened and migrated toward production-grade architecture, not a client engagement with a separate sponsoring organization.

## Business problem

Manual resume screening for technical roles is slow and inconsistent. CDPI automates the pipeline: parse resumes against a job description, score and rank candidates, generate interview questions, schedule interviews, and optionally run an AI voice-bot warm-up interview.

## Sponsorship / ownership

- **Owner & sole developer:** Aditya Jagtap (`adiraj2503@gmail.com` / `aditya.jagtapin@gmail.com`).
- **No named business sponsor, security owner, or compliance owner exists.** This is a known gap — already flagged in the tracker's `FDE_Stage_Tracker` Stage 37 row ("Owner column is currently a single person — single point of failure") and carried forward here rather than invented.
- **Talent Acquisition / Business Lead / Technical Evaluation** exist only as *email recipient roles* in `config/email_templates/*.json` — not staffed by named people today.

## Engagement boundaries & working model

- **Scope:** the existing pipeline (resume parsing → scoring → interview question generation → interview scheduling → voice-bot interview) plus its migration to an Azure-native architecture, per the AI FDE End-to-End Production Delivery Spine.
- **Working model:** solo developer + AI-assisted engineering (this Claude Code engagement). Work proceeds phase-by-phase against the roadmap in `private/CDPI_FDE_Tracker.xlsx`, with the developer approving each phase before the next starts.
- **Out of scope (explicit):** hosting/deploying the frontend (marked Future Scope per developer instruction), external institution/board verification for document intelligence (internal consistency + tamper signals only, for now).

## Initial use-case hypothesis

AI-assisted, semantically-grounded resume screening and scoring reduces manual review time and produces more consistent, explainable candidate rankings than either fully manual review or the project's original ungrounded LLM self-report scoring (see `02-problem-framing-canvas.md` for what "ungrounded" meant in practice, and `03-before-ai-kpi-baseline.md` for the measured evidence).
