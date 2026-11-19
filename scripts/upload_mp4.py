#!/usr/bin/env python3
"""Upload an already-rendered STORK MP4 to a public GitHub repo.

This program NEVER generates video and NEVER publishes to Buffer or Instagram.
Run it in the same network-enabled environment that creates the MP4. Supply a
fine-grained GitHub token with Contents: Read and write through an environment
secret named STORK_GITHUB_TOKEN; never commit the token to this repository.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "cmlsysl-ship-it/stork-reels-automation"
BRANCH = "main"
API_BASE = f"https://api.github.com/repos/{REPO}"


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def verify_mp4(path: Path) -> dict:
    raw_start = path.open("rb").read(40)
    if raw_start[4:8] != b"ftyp":
        raise ValueError("File is not an MP4 (ftyp signature missing)")
    output = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate",
         "-of", "json", str(path)], text=True, timeout=30
    )
    data = json.loads(output)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if len(streams) != 1:
        raise ValueError("Expected exactly one video stream")
    video = streams[0]
    duration = float(data["format"]["duration"])
    if (video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p"
        or video.get("width") != 1080 or video.get("height") != 1920
        or video.get("r_frame_rate") != "30/1" or not 7.95 <= duration <= 8.05):
        raise ValueError(f"MP4 does not match 1080x1920, H.264/yuv420p, 30fps, 8s: {video}, {duration}")
    if path.stat().st_size > 90 * 1024 * 1024:
        raise ValueError("File too large for GitHub Contents API; reduce size")
    return {"duration": duration, "size": path.stat().st_size}


def github(method: str, path: str, token: str, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API_BASE + path, data=body, method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "stork-reels-uploader",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == "GET":
            return 404, None
        # Never print token or the full payload.
        try:
            message = json.loads(exc.read(2048).decode()).get("message", str(exc.code))
        except Exception:
            message = str(exc.code)
        raise RuntimeError(f"GitHub API {method} {path}: HTTP {exc.code} - {message}") from None


def verify_public_bytes(url: str, payload: bytes, attempts: int = 5) -> None:
    """Require an anonymous public download of exactly the source MP4 bytes.

    Buffer and Instagram access URLs without our GitHub bearer token, so a
    successful authenticated GitHub commit is not sufficient evidence.
    """
    expected_hash = hashlib.sha256(payload).hexdigest()
    last_error = "unknown"
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "stork-reels-public-verify",
                              "Accept": "*/*", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=60) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP status {response.status}")
                public_bytes = response.read(len(payload) + 1)
            if len(public_bytes) != len(payload):
                raise RuntimeError("public MP4 byte size does not match local file")
            if hashlib.sha256(public_bytes).hexdigest() != expected_hash:
                raise RuntimeError("public MP4 SHA-256 does not match local file")
            if public_bytes[4:8] != b"ftyp":
                raise RuntimeError("public file is not MP4")
            return
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = str(exc)
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 12))
    raise RuntimeError(f"Public GitHub download verification failed: {last_error}")


def upload(path: Path, token: str, slot: str) -> dict:
    verified = verify_mp4(path)
    payload = path.read_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    blob_sha = git_blob_sha(payload)
    date = dt.datetime.strptime(slot, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
    relative = f"media/{date:%Y/%m/%d}/{date:%H}-{sha256[:12]}.mp4"
    endpoint = "/contents/" + urllib.parse.quote(relative)
    status, existing = github("GET", endpoint + "?ref=" + BRANCH, token)
    if status == 200:
        if existing.get("sha") != blob_sha or existing.get("size") != len(payload):
            raise RuntimeError("Path collision: refusing to overwrite existing MP4")
    else:
        _, result = github("PUT", endpoint, token, {
            "message": f"Store verified STORK MP4 {slot} ({sha256[:12]})",
            "content": base64.b64encode(payload).decode("ascii"), "branch": BRANCH
        })
        if result.get("content", {}).get("sha") != blob_sha:
            raise RuntimeError("GitHub upload did not preserve source MP4 bytes")
    status, check = github("GET", endpoint + "?ref=" + BRANCH, token)
    if status != 200 or check.get("sha") != blob_sha or check.get("size") != len(payload):
        raise RuntimeError("GitHub post-upload SHA/size verification failed")
    url = check.get("download_url")
    if not url or not url.startswith(f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"):
        raise RuntimeError("GitHub did not return the expected public raw-file URL")
    verify_public_bytes(url, payload)
    return {"url": url, "repository_path": relative, "sha256": sha256,
            "git_blob_sha1": blob_sha, **verified, "status": "stored_verified"}


def resolve_github_token() -> str | None:
    """Reuse an existing authorized token or GitHub CLI login, never log it."""
    for name in ("STORK_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        token = os.getenv(name)
        if token:
            return token
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True,
            timeout=8, check=False)
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def main():
    ap = argparse.ArgumentParser(description="Store already-created MP4 to GitHub")
    ap.add_argument("file", type=Path, help="Locally rendered H.264 MP4 file")
    ap.add_argument("--slot", required=True, help="UTC hour YYYY-MM-DDTHH, e.g. 2026-11-19T01")
    args = ap.parse_args()
    if not args.file.is_file():
        ap.error("MP4 file not found")
    token = resolve_github_token()
    if not token:
        ap.error("No usable GitHub token: configure a secure STORK_GITHUB_TOKEN/GH_TOKEN/GITHUB_TOKEN or authenticate GitHub CLI (gh auth login) in the same MP4-producing environment")
    try:
        print(json.dumps(upload(args.file, token, args.slot), ensure_ascii=False))
    except (ValueError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"MP4_UPLOAD_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
