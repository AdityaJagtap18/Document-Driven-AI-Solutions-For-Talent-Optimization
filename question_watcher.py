import os
import time
import queue
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from generate_questions import generate_questions_for_resume

# === Paths ===
watch_folder = "ParsedReadyForQuestions"
watch_folder_path = Path(watch_folder)

# === Queue & State ===
file_queue = queue.Queue()
processed_files = set()

# === Queue Existing PDFs at Startup ===
def queue_existing_files():
    print("[INFO] Checking for existing resumes to process...")
    for file in watch_folder_path.iterdir():
        if file.is_file() and file.suffix.lower() == ".pdf":
            print(f"[INFO] Queuing: {file.name}")
            file_queue.put(str(file))

# === Folder Watcher Class ===
class QuestionWatcher(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".pdf"):
            print(f"[📄] New file detected: {event.src_path}")
            file_queue.put(event.src_path)

# === Background Worker Thread ===
def process_files():
    while True:
        file_path = file_queue.get()
        filename = os.path.basename(file_path)
        if filename not in processed_files:
            try:
                generate_questions_for_resume(file_path)
                processed_files.add(filename)
            except Exception as e:
                print(f"[ERROR] Failed to generate questions for {filename}: {e}")
        file_queue.task_done()

# === Main ===
if __name__ == "__main__":
    print(f"[INFO] Watching folder: {watch_folder_path.resolve()}")
    
    # Step 1: Queue existing files
    queue_existing_files()

    # Step 2: Start background processor thread
    threading.Thread(target=process_files, daemon=True).start()

    # Step 3: Start folder observer
    observer = Observer()
    event_handler = QuestionWatcher()
    observer.schedule(event_handler, str(watch_folder_path), recursive=False)
    observer.start()

    # Step 4: Keep script alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Stopping folder watcher...")
        observer.stop()
    observer.join()
