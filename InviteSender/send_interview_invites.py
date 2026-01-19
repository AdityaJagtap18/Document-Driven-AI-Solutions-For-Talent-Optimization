import smtplib
import pandas as pd
import json
import os
import shutil
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# --- Ensure consistent path resolution ---
current_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(current_dir, "config", "invite_email_config.json")
template_path = os.path.join(current_dir, "config", "interview_invite_template.json")
csv_path = os.path.join(current_dir, "Interview_Slots_Data", "resume_summary.csv")
archive_folder = os.path.join(current_dir, "Invitations_Archive")
meeting_invite_dir = os.path.join(current_dir, "Meeting_Invites")
os.makedirs(archive_folder, exist_ok=True)
os.makedirs(meeting_invite_dir, exist_ok=True)

# === Load Configs ===
with open(config_path) as f:
    email_config = json.load(f)

with open(template_path) as f:
    template = json.load(f)

smtp_email = email_config["smtp_email"]
smtp_password = email_config["smtp_password"]
smtp_server = email_config["smtp_server"]
smtp_port = email_config["smtp_port"]
noreply_email = "noreply.cidp.in@gmail.com"
ta_email = email_config["talent_acquisition_email"]

# === Load CSV ===
df = pd.read_csv(csv_path)

# === Generate and Add Meeting Link ===
def generate_meeting_link(candidate_name):
    formatted_name = candidate_name.strip().replace(" ", "_")
    return f"https://meet.jit.si/Interview_{formatted_name}"

df["Meeting_Link"] = df["Name"].apply(generate_meeting_link)

# === Setup SMTP ===
server = smtplib.SMTP(smtp_server, smtp_port)
server.starttls()
server.login(smtp_email, smtp_password)

# === Track Status ===
sent_candidates = []
skipped_candidates = []


def create_ics(candidate_name, email, date_str, start_time, end_time, location="Online"):
    start_dt = datetime.strptime(f"{date_str} {start_time}", "%d-%m-%Y %I:%M %p")
    end_dt = datetime.strptime(f"{date_str} {end_time}", "%d-%m-%Y %I:%M %p")
    start_fmt = start_dt.strftime("%Y%m%dT%H%M%S")
    end_fmt = end_dt.strftime("%Y%m%dT%H%M%S")

    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//DYPIC//Interview Scheduler//EN
METHOD:REQUEST
BEGIN:VEVENT
UID:{candidate_name.replace(" ", "_")}-{start_fmt}
DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%S')}
DTSTART:{start_fmt}
DTEND:{end_fmt}
SUMMARY:Interview with DYPIC
DESCRIPTION:Interview scheduled for {candidate_name}
LOCATION:{location}
ORGANIZER;CN=DYPIC Interview Team:MAILTO:{noreply_email}
ATTENDEE;CN={candidate_name}:MAILTO:{email}
END:VEVENT
END:VCALENDAR"""
    ics_filename = os.path.join(current_dir, f"{candidate_name.replace(' ', '_')}_interview.ics")
    with open(ics_filename, "w") as f:
        f.write(ics_content)
    return ics_filename

# === Send Invites ===
for _, row in df.iterrows():
    candidate_name = row.get("Name", "Candidate")
    email = row.get("Email", "")
    if not pd.notna(email) or not email.strip():
        skipped_candidates.append((candidate_name, email, "Skipped (No valid email)"))
        continue
    if email.strip().lower() in [smtp_email.lower(), noreply_email.lower()]:
        skipped_candidates.append((candidate_name, email, "Skipped (SMTP/Noreply address)"))
        continue

    interview_date = row["Interview Date"]
    interview_day = row["Interview Day"]
    start_time = row["Interview Start Time"]
    end_time = row["Interview End Time"]
    slot = row["Interview Slot"]
    meeting_link = row["Meeting_Link"]

    subject = template["subject_template"].format(candidate_name=candidate_name)
    body = template["body_template"].format(
        candidate_name=candidate_name,
        interview_date=interview_date,
        interview_day=interview_day,
        interview_start=start_time,
        interview_end=end_time,
        interview_slot=slot,
        interview_link=meeting_link
    )

    msg = MIMEMultipart()
    msg["From"] = smtp_email
    msg["To"] = email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        ics_path = create_ics(candidate_name, email, str(interview_date), start_time, end_time)
        with open(ics_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(ics_path)}")
            msg.attach(part)

        server.sendmail(smtp_email, email, msg.as_string())
        sent_candidates.append((candidate_name, email, "Sent"))
        os.remove(ics_path)

    except Exception as e:
        skipped_candidates.append((candidate_name, email, f"Skipped (Error: {str(e)})"))

# === Summary CSV Report ===
summary_filename = "invite_status_report.csv"
summary_path = os.path.join(archive_folder, summary_filename)
summary_df = pd.DataFrame(sent_candidates + skipped_candidates, columns=["Name", "Email", "Status"])
summary_df.to_csv(summary_path, index=False)

# === Send Confirmation to Talent Acquisition ===
hr_subject = "Interview Invitations Sent"
hr_body = (
    f"{len(sent_candidates)} candidate interview invitations were successfully sent.\n"
    f"{len(skipped_candidates)} candidates were skipped.\n"
    "Please find the attached summary report."
)

hr_msg = MIMEMultipart()
hr_msg["From"] = smtp_email
hr_msg["To"] = ta_email
hr_msg["Subject"] = hr_subject
hr_msg.attach(MIMEText(hr_body, "plain"))

with open(summary_path, "rb") as f:
    part = MIMEBase("application", "octet-stream")
    part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{summary_filename}"')
    hr_msg.attach(part)

server.sendmail(smtp_email, [ta_email], hr_msg.as_string())

# === Archive resume_summary.csv ===
try:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived_csv_path = os.path.join(archive_folder, f"resume_summary_{timestamp}.csv")
    if os.path.exists(csv_path):
        shutil.copy(csv_path, archived_csv_path)
        print(f"CSV archived as: {archived_csv_path}")
    else:
        print("Warning: resume_summary.csv not found for archiving.")
except Exception as e:
    print(f"Error archiving CSV: {e}")

# === Save to Meeting_Invites/meeting_details.csv ===
try:
    meeting_details_path = os.path.join(meeting_invite_dir, "meeting_details.csv")
    df.to_csv(meeting_details_path, index=False)
    print(f"Meeting details saved at: {meeting_details_path}")
except Exception as e:
    print(f"Error saving meeting_details.csv: {e}")

server.quit()
print("All invites sent. Summary sent to Talent Acquisition.")