import os
import time
import subprocess
import threading
import queue
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# === Paths Setup ===
script_dir = os.path.dirname(os.path.abspath(__file__))
watch_folder = os.path.join(script_dir, "InputResume_Folder")

file_queue = queue.Queue()
processed_files = set()

# === Background Worker ===
def process_files():
    while True:
        file_path = file_queue.get()
        filename = os.path.basename(file_path)

        if filename not in processed_files:
            try:
                print(f"[INFO] Processing: {filename}")
                subprocess.call(["python", "ResumeParser.py", file_path])

                processed_files.add(filename)
                print(f"[INFO] ✅ Done: {filename}")
            except Exception as e:
                print(f"[ERROR] Failed to process {filename}: {e}")
        file_queue.task_done()

# === Watcher Class ===
class ResumeWatcher(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".pdf"):
            print(f"\n[INFO] New file detected: {event.src_path}")
            file_queue.put(event.src_path)

# === Check Existing PDFs in Folder ===
def queue_existing_files():
    print("[INFO] Checking for existing resumes in folder...")
    resume_files = [
        os.path.join(watch_folder, f)
        for f in os.listdir(watch_folder)
        if os.path.isfile(os.path.join(watch_folder, f)) and f.lower().endswith(".pdf")
    ]
    for file_path in resume_files:
        print(f"[INFO] Queuing existing file: {file_path}")
        file_queue.put(file_path)

# === Fetch Attachments from Gmail (only from last X minutes) ===
def fetch_attachments_from_gmail(imap_server, email_user, email_pass, save_folder, max_age_minutes=60):
    try:
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_user, email_pass)
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN)')
        email_ids = messages[0].split()

        if not email_ids:
            print("[EMAIL] No new unread emails.")
            return

        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

        for email_id in email_ids:
            res, msg_data = mail.fetch(email_id, "(RFC822)")
            if res != "OK":
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            msg_date = parsedate_to_datetime(msg.get("Date", ""))
            if msg_date and msg_date < cutoff_time:
                print(f"[EMAIL] Skipping old email from {msg_date}")
                continue

            subject = decode_header(msg["Subject"])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode()
            print(f"[EMAIL] New recent email: {subject}")

            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                if part.get('Content-Disposition') is None:
                    continue

                filename = part.get_filename()
                if filename and filename.lower().endswith(".pdf"):
                    filepath = os.path.join(save_folder, filename)
                    with open(filepath, "wb") as f:
                        f.write(part.get_payload(decode=True))
                    print(f"[EMAIL] Downloaded: {filepath}")
                    file_queue.put(filepath)

        mail.logout()
    except Exception as e:
        print(f"[ERROR] Gmail polling failed: {e}")

# === Poll Gmail Periodically ===
def poll_gmail_periodically(imap_server, email_user, email_pass, save_folder, interval=60, max_age_minutes=60):
    while True:
        fetch_attachments_from_gmail(imap_server, email_user, email_pass, save_folder, max_age_minutes)
        time.sleep(interval)

# === Main Execution ===
if __name__ == "__main__":
    print(f"[INFO] Watching folder: {watch_folder}")

    # Step 1: Queue existing files
    queue_existing_files()

    # Step 2: Start folder watcher
    observer = Observer()
    event_handler = ResumeWatcher()
    observer.schedule(event_handler, watch_folder, recursive=False)
    observer.start()

    # Step 3: Start background processor
    threading.Thread(target=process_files, daemon=True).start()

    # Step 4: Gmail Credentials (use App Password for Gmail)
    imap_server = "imap.gmail.com"
    email_user = "cognitivedocumentintelligecepl@gmail.com"
    email_pass = "dekq drdn vmkr dkhq"

    # Step 5: Start Gmail polling in background
    threading.Thread(
        target=poll_gmail_periodically,
        args=(imap_server, email_user, email_pass, watch_folder, 60, 60),
        daemon=True
    ).start()

    # Step 6: Keep script running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Stopping folder watcher...")
        observer.stop()
    observer.join()
