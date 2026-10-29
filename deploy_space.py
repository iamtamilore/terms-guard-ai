#!/usr/bin/env python3
"""
Push the TermsGuard project to the Hugging Face Space.

Two quirks to work around:
1. The Space's pre-receive hook rejects any push that contains binary
   files. faiss indexes, the hero image, and any other large binary
   need to be uploaded via the Python huggingface_hub API, which
   routes through Xet storage instead of git.
2. The `hf upload` CLI command hits the /api/repos/create endpoint,
   which returns HTTP 402 on non-PRO accounts even when the Space
   already exists. The Python API's upload_file method uses a
   different code path (preupload + commit) that works without PRO.

Run from the terms-guard-space/ directory:
    export HF_TOKEN=<token with write scope on newtazer/terms-guard-ai>
    python3 deploy_space.py

The token needs to be a fine-grained token with content-write scope
on newtazer/terms-guard-ai specifically. The token at
~/.config/newtazer/hf.key (canReadGatedRepos: true) does NOT have
write scope and will fail with HTTP 402 / 401.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from huggingface_hub import HfApi
except ImportError:
    print("  huggingface_hub not installed. pip install huggingface_hub")
    raise SystemExit(1)

HERE = Path(__file__).resolve().parent
SPACE_REPO = "newtazer/terms-guard-ai"
SPACE_BRANCH = "main"  # HF Spaces serve from main by default

# Files that the pre-receive hook rejects in plain git pushes.
# Upload these via the Python API first.
BINARY_FILES = [
    "gdpr_kb/index.faiss",
    "gdpr_kb/gloss.faiss",
    "static/img/hero.jpg",
]


def get_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token.strip()
    # Fallback: try to extract from the standard key file. Only used
    # when the file contains a single bare token (no surrounding text).
    key_path = Path.home() / ".config" / "newtazer" / "hf.key"
    if key_path.exists():
        text = key_path.read_text()
        import re
        m = re.search(r"hf_[A-Za-z0-9]+", text)
        if m:
            return m.group(0)
    print("  HF_TOKEN not set and ~/.config/newtazer/hf.key has no token.")
    print("  Set HF_TOKEN to a token with write scope on newtazer/terms-guard-ai.")
    raise SystemExit(1)


def upload_binary(api: HfApi, path_in_repo: str) -> bool:
    local = HERE / path_in_repo
    if not local.exists():
        print(f"  SKIP: {path_in_repo} does not exist locally")
        return True
    try:
        result = api.upload_file(
            path_or_fileobj=str(local),
            path_in_repo=path_in_repo,
            repo_id=SPACE_REPO,
            repo_type="space",
            commit_message=f"Add {path_in_repo}",
        )
        sha = result.split("/commit/")[-1][:12] if "/commit/" in result else "?"
        print(f"  OK  {sha}  {path_in_repo}")
        return True
    except Exception as e:
        print(f"  FAIL {path_in_repo}: {type(e).__name__}: {str(e)[:200]}")
        return False


def upload_text(api: HfApi, path_in_repo: str, message: str) -> bool:
    local = HERE / path_in_repo
    if not local.exists():
        print(f"  SKIP: {path_in_repo} does not exist locally")
        return True
    try:
        result = api.upload_file(
            path_or_fileobj=str(local),
            path_in_repo=path_in_repo,
            repo_id=SPACE_REPO,
            repo_type="space",
            commit_message=message,
        )
        sha = result.split("/commit/")[-1][:12] if "/commit/" in result else "?"
        print(f"  OK  {sha}  {path_in_repo}")
        return True
    except Exception as e:
        print(f"  FAIL {path_in_repo}: {type(e).__name__}: {str(e)[:200]}")
        return False


def main() -> int:
    token = get_token()
    api = HfApi(token=token)

    print(f"  uploading {len(BINARY_FILES)} binary files via Python API")
    for f in BINARY_FILES:
        if not upload_binary(api, f):
            return 1

    print("\n  uploading text files")
    text_files = [
        ("index.html", "Redesign demo UI"),
        ("static/css/design-system.css", "Add design-system.css"),
        ("deploy_space.py", "Update deploy script"),
        ("README.md", "Update README"),
    ]
    for f, msg in text_files:
        if not upload_text(api, f, msg):
            # Don't fail hard on text - keep going
            print(f"  (continuing despite failure on {f})")

    print("\n  done. The Space will rebuild and pick up the new files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
