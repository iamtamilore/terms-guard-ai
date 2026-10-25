#!/usr/bin/env python3
"""
Push the TermsGuard KB to the Hugging Face Space.

The Space's git push hook rejects binary files (the new gdpr_kb/index.faiss
is 845 KB, over HF's 10 MB soft cap for binary files committed through
git, and they want files uploaded via Xet storage instead). This script
calls `hf upload` to push the binary files directly, then pushes the
non-binary files via git.

Run from the terms-guard-space/ directory:
    python3 deploy_space.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPACE_REPO = "newtazer/terms-guard-ai"
SPACE_REVISION = "master"

BINARY_FILES = [
    "gdpr_kb/index.faiss",
    "gdpr_kb/gloss.faiss",
]


def main() -> int:
    hf = shutil.which("hf")
    if not hf:
        print("  hf CLI not found. Install with: pip install -U huggingface_hub")
        return 1

    print(f"  uploading {len(BINARY_FILES)} binary files via Xet storage")
    for f in BINARY_FILES:
        path = HERE / f
        if not path.exists():
            print(f"  SKIP: {f} does not exist")
            continue
        cmd = [
            hf, "upload",
            "--repo-type", "space",
            "--revision", SPACE_REVISION,
            "--include", path.name,
            str(SPACE_REPO),
            str(path),
            str(path.parent),
        ]
        print(f"  {f}", end=" ... ", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"FAILED: {r.stderr.strip()}")
            return 1
        print("OK")

    print("\n  pushing non-binary files via git")
    r = subprocess.run(["git", "push", "space", "master"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  git push failed:\n{r.stdout}\n{r.stderr}")
        return 1
    print(r.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
