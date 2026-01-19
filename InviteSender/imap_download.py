import imaplib
import email
import os
import json

# --- Ensure proper working directory ---
current_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(current_dir, "config", "invite_email_config.json")

with open(config_path, "r") as f:
    config = json.load(f)

imap_server = config["imap_server"]
imap_email = config["imap_email"]
imap_password = config["imap_password"]

# Target download folder
download_folder = os.path.join(current_dir, "Interview_Slots_Data")
os.makedirs(download_folder, exist_ok=True)

# Connect to IMAP
mail = imaplib.IMAP4_SSL(imap_server)
mail.login(imap_email, imap_password)
mail.select("inbox")

# Search for unread emails with attachments
status, messages = mail.search(None, '(UNSEEN)')
messages = messages[0].split()

for msg_id in messages:
    status, msg_data = mail.fetch(msg_id, "(RFC822)")
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1])
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get("Content-Disposition") is None:
                    continue

                filename = part.get_filename()
                if filename and filename.endswith(".csv"):
                    filepath = os.path.join(download_folder, "resume_summary.csv")
                    with open(filepath, "wb") as f:
                        f.write(part.get_payload(decode=True))
                    print(f"Downloaded: {filepath}")

mail.logout()
