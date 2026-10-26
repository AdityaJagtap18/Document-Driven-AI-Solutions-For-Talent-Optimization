"""Generates 10 synthetic resume PDFs and 2 synthetic JD PDFs using
reportlab (already installed -- no new downloads). All data is
fictional/synthetic, consistent with this repo's existing convention of
synthetic test candidates (see docs/fde/*.md references to "E2E test
run, synthetic data"). No real candidate PII."""

import os
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem

ROOT = Path(__file__).resolve().parent.parent
RESUME_OUT = ROOT / "InputResume_Folder"
JD_OUT = ROOT / "scratchpad_resumes" / "jd_uploads_demo"
RESUME_OUT.mkdir(exist_ok=True)
JD_OUT.mkdir(parents=True, exist_ok=True)

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


def build_jd(path, title, summary, responsibilities, required, preferred):
    doc = SimpleDocTemplate(str(path), pagesize=LETTER, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                             leftMargin=0.7 * inch, rightMargin=0.7 * inch)
    story = [
        Paragraph(title, name_style),
        Paragraph("Job Description", contact_style),
        Paragraph("Role Summary", h2), Paragraph(summary, body),
        Paragraph("Responsibilities", h2),
        ListFlowable([ListItem(Paragraph(r, body)) for r in responsibilities], bulletType="bullet", leftIndent=16),
        Paragraph("Required Skills", h2),
        ListFlowable([ListItem(Paragraph(r, body)) for r in required], bulletType="bullet", leftIndent=16),
        Paragraph("Preferred Skills", h2),
        ListFlowable([ListItem(Paragraph(r, body)) for r in preferred], bulletType="bullet", leftIndent=16),
    ]
    doc.build(story)


RESUMES = [
    dict(name="Priya Raman", email="priya.raman.synth@example.com", phone="+1-555-0101",
         linkedin="linkedin.com/in/priya-raman-synth",
         summary="Backend engineer with 3 years building REST APIs and data pipelines in Python, strong SQL and cloud fundamentals.",
         skills=["Python", "Flask", "REST APIs", "PostgreSQL", "Docker", "Git", "Pytest"],
         experience=[("Backend Engineer", "Northwind Data Co (synthetic)", "2023-Present",
                      ["Built and maintained REST APIs serving 50k+ daily requests.",
                       "Migrated batch ETL jobs from cron scripts to a scheduled pipeline, cutting runtime 40%."])],
         projects=[("Inventory Sync Service", "Python/Flask microservice synchronizing warehouse inventory across 3 regional databases.")],
         education=[("B.Tech Computer Science", "Synthetic State University", "2019-2023")]),
    dict(name="Marcus Chen", email="marcus.chen.synth@example.com", phone="+1-555-0102",
         linkedin="linkedin.com/in/marcus-chen-synth",
         summary="Junior ML engineer, internship experience fine-tuning transformer models and building LLM-backed document extraction tools.",
         skills=["Python", "PyTorch", "LLM concepts", "REST API development", "Pandas", "Docker"],
         experience=[("ML Intern", "Vertex Analytics (synthetic)", "Summer 2025",
                      ["Fine-tuned a small transformer for resume field extraction, improving field accuracy 15%.",
                       "Built a Flask API wrapping an LLM-based document classifier."])],
         projects=[("Resume Parsing Prototype", "LLM-assisted document section classification and structured data extraction.")],
         education=[("B.S. Computer Science", "Synthetic Tech Institute", "2021-2025")]),
    dict(name="Aisha Khan", email="aisha.khan.synth@example.com", phone="+1-555-0103",
         linkedin="linkedin.com/in/aisha-khan-synth",
         summary="Full-stack developer with 5 years' experience, strong in Java and Spring Boot, some exposure to Python scripting.",
         skills=["Java", "Spring Boot", "React", "MySQL", "REST APIs", "Jenkins"],
         experience=[("Software Engineer", "Harborlight Systems (synthetic)", "2020-Present",
                      ["Developed and maintained a Spring Boot microservices platform handling payment processing.",
                       "Led migration of a monolith to 6 independently deployable services."])],
         projects=[("Internal Billing Dashboard", "React + Spring Boot dashboard for finance team's monthly billing review.")],
         education=[("B.E. Information Technology", "Synthetic Institute of Technology", "2015-2019")]),
    dict(name="Daniel Osei", email="daniel.osei.synth@example.com", phone="+1-555-0104",
         linkedin="linkedin.com/in/daniel-osei-synth",
         summary="Entry-level software engineer, academic coursework and personal projects in Python and SQL, no professional experience yet.",
         skills=["Python", "SQL", "Data processing", "Debugging", "Git"],
         experience=[],
         projects=[("Student Grade Analyzer", "Python/SQL tool to import, clean and visualize university grade datasets."),
                   ("Personal Budget Tracker", "Flask app with SQLite backend for tracking monthly expenses.")],
         education=[("B.S. Computer Science", "Synthetic State University", "2022-2026")]),
    dict(name="Elena Petrova", email="elena.petrova.synth@example.com", phone="+1-555-0105",
         linkedin="linkedin.com/in/elena-petrova-synth",
         summary="Senior ML/AI engineer, 7 years building production LLM and recommendation systems, strong Python and MLOps background.",
         skills=["Python", "LLM concepts", "RAG", "PyTorch", "Azure OpenAI", "Docker", "Kubernetes"],
         experience=[("Senior ML Engineer", "Crestwave AI (synthetic)", "2019-Present",
                      ["Designed a retrieval-augmented generation pipeline for internal knowledge search, cutting support ticket time 30%.",
                       "Owned the team's LLM cost/latency observability dashboard."])],
         projects=[("Document Q&A Assistant", "RAG-based assistant answering questions over a 10k-document internal corpus.")],
         education=[("M.S. Computer Science, AI specialization", "Synthetic Graduate Institute", "2017-2019")]),
    dict(name="Wei Zhang", email="wei.zhang.synth@example.com", phone="+1-555-0106",
         linkedin="linkedin.com/in/wei-zhang-synth",
         summary="QA/test engineer transitioning into backend development, strong Python scripting and API testing background.",
         skills=["Python", "Selenium", "REST API testing", "SQL", "Pytest", "Postman"],
         experience=[("QA Engineer", "Brightpath Software (synthetic)", "2021-Present",
                      ["Built an automated API test suite in Python/Pytest covering 200+ endpoints.",
                       "Reduced manual regression testing time by 60% via Selenium automation."])],
         projects=[("API Contract Test Framework", "Pytest-based framework validating REST API responses against OpenAPI schemas.")],
         education=[("B.S. Information Systems", "Synthetic Coastal University", "2017-2021")]),
    dict(name="Fatima Al-Sayed", email="fatima.alsayed.synth@example.com", phone="+1-555-0107",
         linkedin="linkedin.com/in/fatima-alsayed-synth",
         summary="Junior software engineer with internship experience in REST API development and basic machine-learning coursework.",
         skills=["Python", "REST API development", "SQL", "Data processing", "Machine learning", "LLM concepts"],
         experience=[("Software Engineering Intern", "Lumenova Labs (synthetic)", "Summer 2025",
                      ["Built input validation and data extraction modules for a document-processing prototype.",
                       "Wrote unit tests achieving 85% coverage on new modules."])],
         projects=[("Resume Parsing Prototype", "Prototype for resume parsing including LLM-assisted section classification."),
                   ("Course Recommender", "scikit-learn based course recommendation tool for a university elective catalog.")],
         education=[("B.S. Computer Science", "Synthetic Technical University", "2021-2025")]),
    dict(name="Robert Nakamura", email="robert.nakamura.synth@example.com", phone="+1-555-0108",
         linkedin="linkedin.com/in/robert-nakamura-synth",
         summary="DevOps engineer with 4 years' experience in CI/CD, cloud infrastructure, and Python automation scripting.",
         skills=["Python", "Terraform", "Azure", "Docker", "CI/CD", "Bash"],
         experience=[("DevOps Engineer", "Ironridge Cloud (synthetic)", "2022-Present",
                      ["Automated infrastructure provisioning with Terraform across 3 Azure environments.",
                       "Built a Python-based deployment pipeline reducing release time from 2 hours to 15 minutes."])],
         projects=[("Infra-as-Code Template Library", "Reusable Terraform modules for common Azure resource patterns.")],
         education=[("B.S. Computer Engineering", "Synthetic Polytechnic", "2018-2022")]),
    dict(name="Grace Mensah", email="grace.mensah.synth@example.com", phone="+1-555-0109",
         linkedin="linkedin.com/in/grace-mensah-synth",
         summary="Recent graduate, strong academic background in machine learning and LLM concepts, one internship in data processing.",
         skills=["Python", "Machine learning", "LLM concepts", "SQL", "Data validation", "Problem-solving"],
         experience=[("Data Intern", "Clearview Analytics (synthetic)", "Summer 2025",
                      ["Cleaned and validated survey datasets covering 10k+ responses.",
                       "Built a basic classification model to flag incomplete survey entries."])],
         projects=[("LLM-based Resume Extraction Prototype", "Flask + LLM API project extracting structured fields from resumes.")],
         education=[("B.S. Data Science", "Synthetic State University", "2022-2026")]),
    dict(name="Tomás Ibarra", email="tomas.ibarra.synth@example.com", phone="+1-555-0110",
         linkedin="linkedin.com/in/tomas-ibarra-synth",
         summary="Mid-level backend developer, 4 years' Java experience, currently upskilling in Python and REST API design.",
         skills=["Java", "Python (learning)", "Spring Boot", "REST APIs", "MySQL"],
         experience=[("Backend Developer", "Stonegate Systems (synthetic)", "2021-Present",
                      ["Maintained a Java/Spring Boot order-management system processing 5k orders/day.",
                       "Completed an internal Python bootcamp and shipped a small internal reporting script."])],
         projects=[("Order Status Webhook Service", "Spring Boot service notifying downstream systems of order status changes.")],
         education=[("B.S. Software Engineering", "Synthetic National University", "2016-2020")]),
]

JDS = [
    dict(title="Junior Software Engineer — AI Document Processing",
         summary="We are hiring a Junior Software Engineer to help build and maintain our AI-powered document processing pipeline, "
                 "including resume parsing, structured data extraction, and candidate-JD matching.",
         responsibilities=["Build and maintain Python services for document ingestion and parsing.",
                            "Work with LLM-based extraction prompts to improve structured field accuracy.",
                            "Write unit and integration tests for new pipeline stages.",
                            "Collaborate with recruiters to refine matching logic."],
         required=["Python", "REST API development", "SQL", "Basic machine learning / LLM concepts", "Debugging"],
         preferred=["Experience with document processing or OCR", "Exposure to Azure OpenAI or similar LLM APIs"]),
    dict(title="Machine Learning Developer",
         summary="We are looking for a Machine Learning Developer to design, train, and deploy ML/LLM-based features across our "
                 "recruitment platform, including retrieval-augmented generation and evaluation tooling.",
         responsibilities=["Design and implement retrieval-augmented generation (RAG) pipelines.",
                            "Build evaluation harnesses for LLM output quality.",
                            "Optimize model routing and caching for cost/latency.",
                            "Partner with backend engineers to integrate ML features into production APIs."],
         required=["Python", "PyTorch or similar", "LLM/RAG concepts", "REST APIs", "Docker"],
         preferred=["Experience with Azure OpenAI", "MLOps / model observability experience"]),
]

for r in RESUMES:
    fname = r["name"].lower().replace(" ", "_") + "_resume.pdf"
    build_resume(RESUME_OUT / fname, r["name"], r["email"], r["phone"], r["linkedin"],
                 r["summary"], r["skills"], r["experience"], r["projects"], r["education"])
    print("wrote", RESUME_OUT / fname)

for jd in JDS:
    fname = jd["title"].lower().replace(" ", "_").replace("—", "").replace("--", "_") + ".pdf"
    fname = "_".join(fname.split())
    build_jd(JD_OUT / fname, jd["title"], jd["summary"], jd["responsibilities"], jd["required"], jd["preferred"])
    print("wrote", JD_OUT / fname)

print("\nDone:", len(RESUMES), "resumes in", RESUME_OUT, "|", len(JDS), "JDs in", JD_OUT)
