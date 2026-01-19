# -*- coding: utf-8 -*-
import os
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# === File Paths ===
CREDENTIALS_FILE = os.path.join(BASE_DIR, "config", "email_credentials.json")
RECIPIENTS_FILE = os.path.join(BASE_DIR, "config", "email_recipients.json")
TEMPLATE_DIR = os.path.join(BASE_DIR, "config", "email_templates")

# === Date Placeholder ===
today_str = datetime.now().strftime("%B %d, %Y")

# === Load Email Credentials ===
try:
    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        creds = json.load(f)
        EMAIL_ADDRESS = creds["sender_email"]
        EMAIL_PASSWORD = creds["app_password"]
except Exception as e:
    print(f"[ERROR] Failed to load credentials: {e}")
    exit(1)

# === Load Recipients ===
try:
    with open(RECIPIENTS_FILE, "r", encoding="utf-8") as f:
        RECIPIENTS = json.load(f)
except Exception as e:
    print(f"[ERROR] Failed to load recipient config: {e}")
    exit(1)

# === Per Group Settings ===
GROUPS = {
    "talent_acquisition": {
        "template_file": "talent_acquisition.json",
        "attachment": os.path.join(BASE_DIR, "Resume_Parsed_CSVs", "resume_summary.csv"),
    },
    "technical_evaluation": {
        "template_file": "technical_evaluation.json",
        "attachment": os.path.join(BASE_DIR, "CandidateMetrics_Folder", "candidate_ranking_metrics.csv"),
    },
    "business_lead": {
        "template_file": "business_lead.json",
        "attachment": os.path.join(BASE_DIR, "Resume_Parsed_CSVs", "resume_summary.csv"),
    }
}

# === Email Sender Function ===
def send_email(group, to_emails, subject, body, attachment_path):
    if not os.path.exists(attachment_path):
        print(f"[WARN] Attachment not found for {group}: {attachment_path}")
        return

    # Flag file to prevent duplicate emails
    flag_path = os.path.join(os.path.dirname(attachment_path), f".email_sent_{group}.flag")
    if os.path.exists(flag_path):
        print(f"[INFO] Email for '{group}' already sent. Skipping.")
        return

    msg = EmailMessage()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with open(attachment_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="octet-stream",
                filename=os.path.basename(attachment_path)
            )
    except Exception as e:
        print(f"[ERROR] Could not attach file for {group}: {e}")
        return

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)

        with open(flag_path, "w") as f:
            f.write("sent")

        print(f"[INFO] Email sent successfully to '{group}' ({', '.join(to_emails)}).")

    except Exception as e:
        print(f"[ERROR] Failed to send email for {group}: {e}")

# === Main Execution ===
for group_key, settings in GROUPS.items():
    to_emails = RECIPIENTS.get(group_key, [])
    if not to_emails:
        print(f"[SKIP] No recipients defined for group '{group_key}'.")
        continue

    template_path = os.path.join(TEMPLATE_DIR, settings["template_file"])
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template = json.load(f)
            subject = template.get("subject", "Candidate Report").format(date=today_str)
            body = template.get("body", "Attached report.").format(date=today_str)
    except Exception as e:
        print(f"[ERROR] Failed to load template for {group_key}: {e}")
        continue

    send_email(
        group=group_key,
        to_emails=to_emails,
        subject=subject,
        body=body,
        attachment_path=settings["attachment"]
    )
