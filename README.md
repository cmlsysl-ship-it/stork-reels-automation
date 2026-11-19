# STORK Reels — GitHub video storage

**Architecture:** ChatGPT generates an MP4; GitHub stores its exact bytes; Buffer receives a public MP4 URL and publishes the Instagram Reel. GitHub must NOT generate recipe videos.

## Verified on 2026-11-19
- An independently rendered 8-second H.264 MP4 was uploaded using the GitHub connector's `create_blob → create_tree → create_commit → update_ref` sequence.
- Git object SHA matched the locally computed blob SHA; the file appears at `media/transfer-validation-2026-11-19.mp4`.
- Buffer accepted its `raw.githubusercontent.com` URL as a `video/mp4` Reel draft with 8000ms duration. The test draft was deleted, not published.
- This is an infrastructure smoke test, NOT a successful Instagram publication or a full-size 1080×1920 hourly transfer.

## Unattended full-size upload helper
`scripts/upload_mp4.py` runs where ChatGPT's video is already rendered. It **does not generate video**. It validates the H.264 1080×1920, 30fps, 8-second MP4, uploads bytes directly to this repository through GitHub's HTTPS API, and verifies the Git blob SHA and byte size.

Requirements: Python 3.10+, ffprobe, outbound HTTPS, and authenticated GitHub write access in the **same environment that rendered the MP4**. The uploader accepts `STORK_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, or an existing authenticated GitHub CLI session (`gh auth login` → `gh auth token`); do not put credentials in GitHub code, chat messages, URLs, or published videos. The ChatGPT GitHub connector's OAuth credentials are not automatically exported to local Python processes.

```bash
python scripts/upload_mp4.py /absolute/path/new-reel.mp4 --slot 2026-11-19T01
```

The `--slot` argument is the target **UTC hour**. Output includes a verified public direct file URL, SHA-256, Git blob SHA, and file size. The helper also downloads the newly uploaded public MP4 anonymously and checks its exact bytes against the original file before reporting success. Pass the URL to Buffer only after verifying that no Reel exists in the target hour. Repeated invocations with the same bytes are idempotent; the uploader refuses path collisions.

**Pending before production claim:** ensure a network-enabled MP4-producing runtime with a usable GitHub credential or reliable base64 bridge to the ChatGPT GitHub connector; verify a real 1080×1920 MP4 upload over public HTTPS; confirm an original Instagram `sent` Reel via Buffer. The GitHub repo does not itself create the MP4. No test MP4 may be published. 
