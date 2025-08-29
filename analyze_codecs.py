#!/usr/bin/env python3

import requests
import sys
import time

# --- CONFIGURATION ---
JELLYFIN_URL = "http://localhost:8096"         # Change if your server is remote
API_KEY = "API_KEY_HERE"   # Replace with your real API key
USER_ID = "USER_ID_HERE"   # Replace with your user ID
SAVE_RATIO = 0.40                              # 40% estimated savings
OUTPUT_FILE = "transcode_list.txt"             # Output file

# Inefficient codecs to transcode
INEFFICIENT_CODECS = {"h264", "mpeg4", "vc1"}

HEADERS = {
    "X-Emby-Token": API_KEY
}

def get_all_items():
    url = f"{JELLYFIN_URL}/Users/{USER_ID}/Items"
    params = {
        "IncludeItemTypes": "Movie,Episode",
        "Recursive": "true",
        "Fields": "MediaStreams"
    }
    response = requests.get(url, headers=HEADERS, params=params)
    if response.status_code != 200:
        print(f"Failed to get items: {response.status_code} {response.text}")
        sys.exit(1)
    return response.json()

def get_item_playback_info(item_id):
    url = f"{JELLYFIN_URL}/Items/{item_id}/PlaybackInfo"
    params = {"UserId": USER_ID}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Error fetching playback info for {item_id}: {e}")
    return None

def analyze_codecs_and_collect_paths(data):
    codec_stats = {}
    total_bytes = 0
    transcode_bytes = 0
    transcode_candidates = []  # (size_bytes, path)

    items = data.get("Items", [])
    total_items = len(items)
    print(f"\n📊 Analyzing {total_items} items...")

    for i, item in enumerate(items, 1):
        media_streams = item.get("MediaStreams", [])
        video_codec = None
        for stream in media_streams:
            if stream.get("Type") == "Video":
                video_codec = stream.get("Codec")
                break

        if not video_codec:
            continue

        item_id = item.get("Id")
        playback_info = get_item_playback_info(item_id)
        if not playback_info:
            continue

        media_source = playback_info.get("MediaSources", [{}])[0]
        size = media_source.get("Size", 0) or 0
        path = media_source.get("Path", "") or ""

        video_codec = (video_codec or "").lower()
        codec_stats[video_codec] = codec_stats.get(video_codec, {"count": 0, "bytes": 0})
        codec_stats[video_codec]["count"] += 1
        codec_stats[video_codec]["bytes"] += size
        total_bytes += size

        if video_codec in INEFFICIENT_CODECS and path:
            transcode_candidates.append((size, path))
            transcode_bytes += size

        if i % 500 == 0 or i == total_items:
            print(f"Processed {i}/{total_items} items...")

    print("\n===== Codec Analysis =====")
    for codec, stats in sorted(codec_stats.items(), key=lambda x: -x[1]["count"]):
        gb = stats["bytes"] / (1024 ** 3)
        print(f"{codec.upper():>7}: {stats['count']:>5} files, {gb:.2f} GB")

    print("\n===== Transcode Summary =====")
    print(f"Total to Transcode: {len(transcode_candidates)}")
    print(f"Size to Transcode : {transcode_bytes / (1024 ** 3):.2f} GB")
    print(f"Estimated Savings  : {(transcode_bytes * SAVE_RATIO) / (1024 ** 3):.2f} GB")

    # Sort by size (largest first) and return just the paths
    sorted_paths = [p for s, p in sorted(transcode_candidates, key=lambda x: x[0], reverse=True)]
    return sorted_paths

def save_paths_to_file(paths, filename):
    with open(filename, "w") as f:
        for path in paths:
            f.write(path + "\n")
    print(f"\n📁 File paths saved to: {filename}")

if __name__ == "__main__":
    start = time.time()
    print("🔍 Starting inefficient codec scan...")
    data = get_all_items()
    paths = analyze_codecs_and_collect_paths(data)
    save_paths_to_file(paths, OUTPUT_FILE)
    end = time.time()

