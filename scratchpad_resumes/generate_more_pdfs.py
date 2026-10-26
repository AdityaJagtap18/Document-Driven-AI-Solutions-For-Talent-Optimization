"""Two more synthetic resumes, deliberately weaker fits for the real
active JDs (Junior SWE -- AI Document Processing / ML Developer), so the
real AI scoring pipeline genuinely produces a "To be Considered" and a
"Rejected" tier -- not hardcoded, the real LLM judges these for real."""

from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem

ROOT = Path(__file__).resolve().parent.parent
RESUME_OUT = ROOT / "InputResume_Folder"
RESUME_OUT.mkdir(exist_ok=True)

styles = getSampleStyleSheet()
name_style = ParagraphStyle("NameStyle", parent=styles["Title"], fontSize=18, spaceAfter=2)
contact_style = ParagraphStyle("ContactStyle", parent=styles["Normal"], fontSize=9, textColor=colors.grey, spaceAfter=10)
h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#2c2a5e"))
body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14)


def build_resume(path, name, email, phone, linkedin, summary, skills, experience, projects, education):
    doc = SimpleDocTemplate(str(path), pagesize=LETTER, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                             leftMargin=0.7 * inch, rightMargin=0.7 * inch)
    story = [
        Paragraph(name, name_style),
        Paragraph(f"{email} | {phone} | {linkedin}", contact_style),
        Paragraph("Summary", h2), Paragraph(summary, body),
        Paragraph("Skills", h2), Paragraph(", ".join(skills), body),
        Paragraph("Experience", h2),
    ]
    for title, org, dates, bullets in experience:
        story.append(Paragraph(f"<b>{title}</b> — {org} ({dates})", body))
        story.append(ListFlowable([ListItem(Paragraph(b, body)) for b in bullets], bulletType="bullet", leftIndent=16))
        story.append(Spacer(1, 6))
    story.append(Paragraph("Projects", h2))
    for title, desc in projects:
        story.append(Paragraph(f"<b>{title}</b> — {desc}", body))
        story.append(Spacer(1, 4))
    story.append(Paragraph("Education", h2))
    for degree, school, dates in education:
        story.append(Paragraph(f"{degree}, {school} ({dates})", body))
    doc.build(story)


RESUMES = [
    # Borderline fit: real IT/support background, SOME SQL/scripting
    # exposure, but no Python/REST API/ML experience at all -- a
    # genuinely moderate, not-clearly-strong-or-weak match.
    dict(name="Jordan Lee", email="jordan.lee.synth@example.com", phone="+1-555-0111",
         linkedin="linkedin.com/in/jordan-lee-synth",
         summary="IT support specialist with 4 years' experience troubleshooting business systems, basic SQL "
                 "reporting, and some exposure to shell scripting for routine automation tasks.",
         skills=["Windows/Linux administration", "SQL (basic reporting)", "Bash scripting", "Ticketing systems", "Networking fundamentals"],
         experience=[("IT Support Specialist", "Meadowbrook Logistics (synthetic)", "2021-Present",
                      ["Resolved 30+ support tickets weekly across desktop, network, and account-access issues.",
                       "Wrote basic SQL queries to generate weekly inventory reports.",
                       "Automated routine server health checks with simple Bash scripts."])],
         projects=[("Ticket Volume Dashboard", "Basic Excel/SQL dashboard tracking support-ticket volume by category.")],
         education=[("A.S. Information Technology", "Synthetic Community College", "2019-2021")]),

    # Clear mismatch: no technical/programming background at all --
    # a real, honest non-fit for a software engineering role.
    dict(name="Taylor Brooks", email="taylor.brooks.synth@example.com", phone="+1-555-0112",
         linkedin="linkedin.com/in/taylor-brooks-synth",
         summary="Marketing coordinator with 5 years' experience in social media campaigns, event planning, and "
                 "customer outreach for a retail brand. Looking to explore new career opportunities.",
         skills=["Social media marketing", "Content scheduling", "Event planning", "Customer outreach", "Adobe Creative Suite"],
         experience=[("Marketing Coordinator", "Brightleaf Retail Group (synthetic)", "2020-Present",
                      ["Managed social media calendars across 3 platforms, growing engagement 40% year over year.",
                       "Coordinated 12 in-store promotional events annually.",
                       "Built customer email campaigns using a drag-and-drop marketing platform (no coding)."])],
         projects=[("Spring Promotion Campaign", "Led a seasonal marketing campaign across social and email channels.")],
         education=[("B.A. Marketing", "Synthetic Business University", "2016-2020")]),
]

for r in RESUMES:
    fname = r["name"].lower().replace(" ", "_") + "_resume.pdf"
    build_resume(RESUME_OUT / fname, r["name"], r["email"], r["phone"], r["linkedin"],
                 r["summary"], r["skills"], r["experience"], r["projects"], r["education"])
    print("wrote", RESUME_OUT / fname)
