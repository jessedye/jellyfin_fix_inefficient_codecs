#!/usr/bin/env python3

import os
import sys
import time
import fcntl
import threading
import subprocess
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--input", default="transcode_list.txt")
parser.add_argument("--base", default="/data")
parser.add_argument("--log", default="transcode_failures.log")
parser.add_argument("--workers", type=int, default=os.cpu_count(),
                    help="Number of concurrent ffmpeg workers (default: os.cpu_count())")
parser.add_argument("--idle-wait", type=float, default=0.5,
                    help="Seconds to wait if no work available right now")
args = parser.parse_args()

input_file = args.input
base_path = args.base
log_file = args.log
workers = max(1, args.workers)
idle_wait = max(0.05, args.idle_wait)

ffmpeg_path = "/usr/bin/ffmpeg"
ffprobe_path = "/usr/bin/ffprobe"
test_limit = None  # Set to None for full batch

inefficient_codecs = {"h264", "mpeg4", "vc1"}

list_file_lock = threading.Lock()  # guards open/close; actual exclusion is via fcntl inside

def get_video_codec(file_path):
    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=True
        )
        codec = result.stdout.strip().lower()
        return codec if codec else None
    except subprocess.CalledProcessError as e:
        print(f"⚠️ ffprobe failed on file: {file_path}\n{e.stderr}")
        return None

def make_item_lock(path):
    """Create a per-file lock to prevent duplicate processing across threads."""
    lock_path = path + ".transcode.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
        return lock_path
    except FileExistsError:
        return None

def release_item_lock(lock_path):
    try:
        if lock_path and os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:
        pass

def acquire_next_job_from_list():
    """
    Atomically: read list, find the first item whose per-file lock we can acquire,
    remove it from the list, and return its path + lock path. If none available, return (None, None).
    """
    if not os.path.exists(input_file):
        return None, None

    with list_file_lock:
        with open(input_file, "r+", encoding="utf-8") as f:
            # lock the list file exclusively while we manipulate lines
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            lines = [ln.strip() for ln in f if ln.strip()]
            if not lines:
                # empty list
                f.seek(0)
                f.truncate()
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return None, None

            # Try to find a line we can lock
            chosen_index = None
            chosen_rel = None
            chosen_full = None
            chosen_lock = None

            for idx, rel in enumerate(lines):
                rel_clean = rel.strip('"').strip("'")
                full_path = os.path.join(base_path, rel_clean.lstrip("/"))
                lock_path = make_item_lock(full_path)
                if lock_path:
                    chosen_index = idx
                    chosen_rel = rel_clean
                    chosen_full = full_path
                    chosen_lock = lock_path
                    break

            if chosen_index is None:
                # All candidates currently locked by other threads — leave file untouched
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return None, None

            # Remove the chosen line and rewrite remaining
            del lines[chosen_index]
            f.seek(0)
            for ln in lines:
                f.write(ln + "\n")
            f.truncate()

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    return (chosen_full, chosen_lock)

def log_failure(path):
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(path + "\n")
    except Exception:
        pass

def transcode_file(original_path):
    print(f"📁 Checking path: {repr(original_path)}")
    if not os.path.isfile(original_path):
        print(f"❌ File not found: {original_path}")
        return False

    codec = get_video_codec(original_path)
    if not codec:
        print(f"⚠️ Could not detect codec: {original_path}")
        return False
    if codec not in inefficient_codecs:
        print(f"⏭️ Skipping efficient codec ({codec}): {original_path}")
        return True  # Count as processed (no transcode needed)

    original_path = os.path.abspath(original_path)
    dir_name = os.path.dirname(original_path)
    base_name = os.path.basename(original_path)
    backup_path = original_path + ".old"
    temp_output = os.path.join(dir_name, f".{base_name}.tmp.mkv")

    print(f"\n🎬 Transcoding ({codec}): {original_path}")
    print(f"🔒 Backing up original to: {backup_path}")

    try:
        os.rename(original_path, backup_path)
    except Exception as e:
        print(f"❌ Failed to rename original file: {e}")
        return False

    ffmpeg_cmd = [
        ffmpeg_path,
        "-y",
        "-hwaccel", "cuda",
        "-i", backup_path,
        "-c:v", "hevc_nvenc",
        "-preset", "p4",
        "-cq", "28",
        "-c:a", "copy",
        temp_output
    ]

    try:
        subprocess.run(ffmpeg_cmd, check=True)
        os.rename(temp_output, original_path)
        os.remove(backup_path)
        print(f"✅ Transcoding complete and original deleted: {original_path}")
        return True

    except subprocess.CalledProcessError as e:
        print(f"❌ Transcoding failed: {e}")
        try:
            # Attempt to restore original
            if os.path.exists(backup_path):
                os.rename(backup_path, original_path)
        except Exception:
            print("⚠️ Failed to restore original file.")
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except Exception:
                pass
        return False

def worker(worker_id, counter_lock, global_counter):
    processed_local = 0
    while True:
        job_path, lock_path = acquire_next_job_from_list()
        if job_path is None:
            # No available work right now — either empty or all items locked, wait and retry
            time.sleep(idle_wait)
            # Re-check once more; if still nothing, see if the list is truly empty
            job_path, lock_path = acquire_next_job_from_list()
            if job_path is None:
                # Check if list file is empty; if yes, exit; otherwise keep looping
                with list_file_lock:
                    if os.path.exists(input_file):
                        with open(input_file, "r", encoding="utf-8") as f:
                            remaining = any(ln.strip() for ln in f)
                    else:
                        remaining = False
                if not remaining:
                    print(f"[W{worker_id}] 🏁 No work left. Exiting.")
                    break
                else:
                    continue  # Items exist but currently locked; retry loop

        try:
            ok = transcode_file(job_path)
            if not ok:
                # Log failures using relative path if we can derive it
                rel = job_path
                if job_path.startswith(base_path.rstrip("/") + "/"):
                    rel = job_path[len(base_path.rstrip("/"))+1:]
                log_failure(rel)
        finally:
            # Always release the per-item lock
            release_item_lock(lock_path)

        processed_local += 1
        if test_limit is not None and processed_local >= test_limit:
            break

        # Optional shared counter (not strictly required)
        with counter_lock:
            global_counter[0] += 1

def main():
    print(f"🚀 Starting transcode job with {workers} workers...")
    if not os.path.exists(input_file):
        print(f"❌ File not found: {input_file}")
        sys.exit(1)

    # Quick notice if file is empty up front
    with open(input_file, "r", encoding="utf-8") as f:
        if not any(ln.strip() for ln in f):
            print("✅ Input list is empty. Nothing to do.")
            return

    threads = []
    counter_lock = threading.Lock()
    global_counter = [0]  # mutable int

    for i in range(workers):
        t = threading.Thread(target=worker, args=(i+1, counter_lock, global_counter), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print("\n✅ Transcode run complete.")

if __name__ == "__main__":
    main()

