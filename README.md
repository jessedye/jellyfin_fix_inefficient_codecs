# Jellyfin Codec Analyzer & Transcoder

This project provides two Python scripts that work together to **analyze and transcode media files in a Jellyfin server library**.  
The goal is to **save disk space** by re‑encoding older or inefficient codecs (e.g., H.264, MPEG4, VC1) into the modern and more space‑efficient **H.265/HEVC** codec.

---

## Overview

1. **`analyze_codecs.py`**  
   - Connects to your Jellyfin server using the REST API.  
   - Scans all movies and episodes in your library.  
   - Identifies inefficient video codecs.  
   - Estimates potential storage savings if re‑encoded to H.265.  
   - Exports a list of file paths to `transcode_list.txt`.

2. **`transcode.py`**  
   - Reads `transcode_list.txt`.  
   - Uses **FFmpeg with NVIDIA NVENC** hardware acceleration to transcode inefficient codecs into H.265.  
   - Runs multiple workers in parallel.  
   - Ensures fault tolerance with file locking, per‑item locks, backups, and failure logging.  

---

## Requirements

- **Python 3.8+**
- **Jellyfin server** with API access enabled
- **FFmpeg** and **FFprobe** installed at `/usr/bin/ffmpeg` and `/usr/bin/ffprobe`
- **NVIDIA GPU** with NVENC support (for fast transcoding)
- Python dependency:
  ```bash
  pip install requests
  ```

---

## Script 1: Analyze Codecs

### Configuration
Edit the top of `analyze_codecs.py`:

```python
JELLYFIN_URL = "http://localhost:8096"  # Your Jellyfin server
API_KEY      = "API_KEY_HERE"           # Replace with your Jellyfin API key
USER_ID      = "USER_ID_HERE"           # Replace with your Jellyfin user ID
SAVE_RATIO   = 0.40                     # Approximate storage savings ratio
OUTPUT_FILE  = "transcode_list.txt"     # Exported list of file paths
```

### Run
```bash
python3 analyze_codecs.py
```

### Output
- Prints codec statistics and estimated savings.
- Saves transcoding candidates into `transcode_list.txt`.

**Example:**

```
===== Codec Analysis =====
   H264:  450 files, 2.35 GB
   VC1  :   23 files, 0.87 GB

===== Transcode Summary =====
Total to Transcode: 473
Size to Transcode : 3.22 GB
Estimated Savings  : 1.29 GB
```

---

## Script 2: Transcode Files

### Quick Start (2 workers, background)
```bash
nohup python3 transcode.py --input transcode_list.txt --workers 2 > transcode.log 2>&1 &
```

### Common Options
- `--input` : Path to the list of files (`transcode_list.txt`).  
- `--base`  : Base directory for media files (default `/data`).  
- `--log`   : Log file for failed transcodes (`transcode_failures.log`).  
- `--workers` : Number of concurrent FFmpeg jobs (default: CPU cores).  
- `--idle-wait` : Seconds a worker waits when no job is immediately available (default: 0.5).  

### Monitoring
- Check progress and errors in the log:
  ```bash
  tail -f transcode.log
  ```
- Monitor GPU usage:
  ```bash
  watch -n 2 nvidia-smi
  ```

### What the script does
- Each file is **exclusively locked** so no duplicates run concurrently.  
- A `.old` backup of the original file is created during transcoding.  
- If transcoding **succeeds**: backup is deleted and the new file replaces the original path.  
- If transcoding **fails**: the original is restored and the path is appended to `transcode_failures.log`.  

---

## 🛠 Example Workflow

1. Scan Jellyfin library and generate the transcode list:
   ```bash
   python3 analyze_codecs.py
   ```
2. Start transcoding (2 workers, as a background job):
   ```bash
   nohup python3 transcode.py --input transcode_list.txt --workers 2 > transcode.log 2>&1 &
   ```
3. Monitor:
   ```bash
   tail -f transcode.log
   nvidia-smi
   ```

---

## Files Created

- **`transcode_list.txt`** — list of files needing transcoding.  
- **`transcode.log`** — runtime logs from the transcoder (when using `nohup`).  
- **`transcode_failures.log`** — list of files that failed to transcode.  
- **`*.old` files** — temporary backups, removed if transcoding succeeds.  

---

## Purpose

These scripts help reduce media storage usage by **automatically upgrading old or inefficient codecs to H.265/HEVC** while maintaining compatibility in Jellyfin. On large libraries, this can result in **significant space savings (≈30–50%)**.

---

## 📝 Notes & Tips

- Ensure `ffmpeg`/`ffprobe` are available at the paths in `transcode.py` or adjust:
  ```python
  ffmpeg_path = "/usr/bin/ffmpeg"
  ffprobe_path = "/usr/bin/ffprobe"
  ```
- Confirm NVENC support (`ffmpeg -encoders | grep nvenc`) and that the NVIDIA driver is loaded.
- If your media root is an S3/remote mount, make sure it supports atomic renames (used for `.old` backups).
- You can re‑run `transcode.py` safely; per‑file lock files (`.transcode.lock`) avoid duplicate processing.
