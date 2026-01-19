import os
import time

def run_imap():
    print("Checking inbox for interview summary...")
    os.system("python imap_download.py")

def run_smtp():
    print("Sending interview invitations...")
    os.system("python send_interview_invites.py")

def check_file_exists():
    return os.path.exists("Interview_Slots_Data\\resume_summary.csv")

# === Main Loop ===
while True:
    run_imap()
    if check_file_exists():
        run_smtp()
        break  # Exit after sending invites
    print("No interview CSV found. Retrying in 60 seconds...\n")
    time.sleep(60)  # Wait 1 minute before checking again

print("Done. Exiting in 5 seconds...")
time.sleep(5)
