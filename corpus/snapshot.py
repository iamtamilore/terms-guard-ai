#!/usr/bin/env python3
"""
TermsGuard corpus snapshot script.

For each of the 20 companies in snapshot.yml:
  1. Fetch the privacy policy URL (with Wayback Machine fallback).
  2. Strip HTML to plain text. Normalise whitespace.
  3. Save the text to corpus/snapshots/<slug>/<YYYY-MM-DD>.txt.
  4. Score the policy via TermsGuard's offline test client.
  5. Save the JSON score to corpus/snapshots/<slug>/<YYYY-MM-DD>.json.
  6. Diff against the most recent prior snapshot. Save to <YYYY-MM-DD>.diff.
  7. If anything fails, write an .error.txt with the reason and continue.

Usage:
    python3 corpus/snapshot.py            # fetch + score all 20
    python3 corpus/snapshot.py --slug google   # one company
    python3 corpus/snapshot.py --limit 5      # first 5 companies only

Output: a tree under corpus/snapshots/ with one folder per company.
"""
import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from html.parser import HTMLParser

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_REQUESTS = False

try:
    import yaml
except ImportError:
    print("  PyYAML not installed. pip install pyyaml")
    raise SystemExit(1)


HERE = Path(__file__).resolve().parent
SNAPSHOTS = HERE / "snapshots"
CONFIG = HERE / "snapshot.yml"

# Browser-like headers. Some sites (BNP Paribas, Santander, H&M, etc.)
# return 403/404 to plain urllib but serve content to a real browser UA
# with the right Accept headers. We send a modern Chrome UA so we don't
# get bot-blocked.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-CH-UA": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Linux"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

WAYBACK_BASE = "https://web.archive.org/web/0/"
FETCH_TIMEOUT = 30  # seconds
SCORE_TIMEOUT = 120  # seconds for the whole scoring call


# ---------- HTML to text ----------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Strip HTML to plain text. Drop scripts, styles, nav. Keep text content."""
    def __init__(self):
        super().__init__()
        self.out = []
        self.skip_depth = 0
        self.block_tags = {
            "p", "div", "section", "article", "header", "footer",
            "li", "ul", "ol", "tr", "td", "th", "br", "h1", "h2",
            "h3", "h4", "h5", "h6",
        }

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "nav", "header", "footer"):
            self.skip_depth += 1
        elif tag in self.block_tags:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "nav", "header", "footer") and self.skip_depth > 0:
            self.skip_depth -= 1
        elif tag in self.block_tags:
            self.out.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.out.append(data)


def html_to_text(html_bytes: bytes) -> str:
    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        html = html_bytes.decode("latin-1", errors="replace")
    s = _HTMLStripper()
    try:
        s.feed(html)
    except Exception:
        pass
    text = "".join(s.out)
    # Collapse whitespace runs but preserve paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------- HTTP fetch ------------------------------------------------------

def fetch(url: str, timeout: int = FETCH_TIMEOUT, max_retries: int = 3) -> bytes:
    """Fetch a URL with exponential backoff on 429/5xx. Used for the live fetch."""
    last_err = None
    for attempt in range(max_retries):
        try:
            if HAS_REQUESTS:
                r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout,
                                 allow_redirects=True)
                if r.status_code == 429:
                    # rate limited; back off and retry
                    wait = 5 * (2 ** attempt)
                    print(f"      rate-limited (429); sleeping {wait}s before retry {attempt + 2}/{max_retries}")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.content
            req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                wait = 3 * (2 ** attempt)
                print(f"      fetch attempt {attempt + 1} failed: {type(e).__name__}; sleeping {wait}s before retry")
                time.sleep(wait)
                continue
            raise
    raise last_err  # unreachable


def fetch_with_wayback(url: str) -> tuple:
    """Returns (bytes, source_url). source_url is 'live' or 'wayback'."""
    try:
        return fetch(url), url
    except Exception as e:
        # Fall back to Wayback Machine. Also retries with backoff.
        wurl = WAYBACK_BASE + url
        try:
            return fetch(wurl), wurl
        except Exception as e2:
            raise RuntimeError(f"live fetch failed: {e}; wayback fetch failed: {e2}")


# ---------- Config ----------------------------------------------------------

def load_config():
    with CONFIG.open() as f:
        return yaml.safe_load(f)["companies"]


# ---------- Scoring ----------------------------------------------------------

def score(text: str) -> dict:
    """Score the policy text via TermsGuard's Flask test client."""
    # Import inside the function so model loading happens once at first call.
    # The script lives in corpus/, so the parent dir (containing app.py)
    # must be on sys.path before we can import it.
    sys.path.insert(0, str(HERE.parent))
    from app import app
    with app.test_client() as c:
        r = c.post("/process", json={"text": text, "links": [], "url": ""})
        if r.status_code != 200:
            raise RuntimeError(f"score returned {r.status_code}")
        return r.get_json()


def summarise(result: dict) -> dict:
    """Reduce a /process response to the fields the corpus cares about."""
    flagged = result.get("non_ethical_points", [])
    cleared = result.get("ethical_points", [])
    unclear = result.get("unclear_points", [])
    avg_flagged = sum(p.get("confidence", 0) for p in flagged) / max(len(flagged), 1)
    avg_cleared = sum(p.get("confidence", 0) for p in cleared) / max(len(cleared), 1)
    return {
        "risk_level": result.get("risk_level", "Unknown"),
        "clauses_analyzed": result.get("clauses_analyzed", 0),
        "flagged_count": len(flagged),
        "cleared_count": len(cleared),
        "uncertain_count": len(unclear),
        "avg_flagged_confidence": round(avg_flagged, 3),
        "avg_cleared_confidence": round(avg_cleared, 3),
        "score": round(avg_flagged, 3),  # primary score: mean confidence of flagged clauses
        "summary": result.get("summary", ""),
        "classification_method": result.get("classification_method", ""),
        "processing_seconds": result.get("processing_seconds", 0),
    }


# ---------- Snapshot one company -------------------------------------------

def snapshot_one(company: dict, today: str, outdir: Path) -> dict:
    """Returns a status dict: ok / skipped / failed with a reason."""
    slug = company["slug"]
    text_path = outdir / f"{today}.txt"
    json_path = outdir / f"{today}.json"
    error_path = outdir / f"{today}.error.txt"

    print(f"  [{slug}] fetching {company['url']}")
    try:
        body, source = fetch_with_wayback(company["url"])
    except Exception as e:
        error_path.write_text(f"fetch failed: {e}\n")
        return {"slug": slug, "ok": False, "reason": f"fetch: {e}"}

    text = html_to_text(body)
    if len(text) < 200:
        error_path.write_text(f"too little text: {len(text)} chars from {source}\n")
        return {"slug": slug, "ok": False, "reason": f"too little text: {len(text)} chars"}

    text_path.write_text(text)
    print(f"    text: {len(text)} chars (source: {source})")

    # Score
    try:
        result = score(text)
        summary = summarise(result)
        json_path.write_text(json.dumps({
            "fetched_at": today,
            "source": source,
            "url": company["url"],
            "summary": summary,
            "non_ethical_points": result.get("non_ethical_points", []),
            "ethical_points": result.get("ethical_points", []),
            "unclear_points": result.get("unclear_points", []),
        }, indent=2))
        print(f"    score: {summary['score']} ({summary['risk_level']})")
    except Exception as e:
        error_path.write_text(f"score failed: {e}\n")
        return {"slug": slug, "ok": False, "reason": f"score: {e}"}

    # Diff against most recent prior text snapshot
    prior_texts = sorted([
        p for p in outdir.glob("*.txt")
        if p.name != text_path.name
    ])
    if prior_texts:
        prior = prior_texts[-1].read_text()
        diff = list(difflib.unified_diff(
            prior.splitlines(), text.splitlines(),
            fromfile=f"snapshot-{prior_texts[-1].stem}",
            tofile=f"snapshot-{today}",
            lineterm="", n=2,
        ))
        diff_text = "\n".join(diff)
        (outdir / f"{today}.diff").write_text(diff_text)
        # Count net change
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        summary["changed_lines"] = added - removed
        print(f"    diff: +{added} -{removed} lines (vs {prior_texts[-1].stem})")
    else:
        (outdir / f"{today}.diff").write_text("")  # empty placeholder
        summary["changed_lines"] = 0
        print(f"    diff: first snapshot, no prior")

    # Re-write the summary JSON with diff info merged
    existing = json.loads(json_path.read_text())
    existing["summary"]["changed_lines"] = summary["changed_lines"]
    existing["summary"]["prior_snapshot"] = str(prior_texts[-1].stem) if prior_texts else None
    json_path.write_text(json.dumps(existing, indent=2))

    return {
        "slug": slug,
        "ok": True,
        "text_chars": len(text),
        "score": summary["score"],
        "risk": summary["risk_level"],
        "flagged": summary["flagged_count"],
        "changed_lines": summary["changed_lines"],
    }


# ---------- Main -------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", help="only snapshot one company by slug")
    ap.add_argument("--limit", type=int, help="only snapshot the first N companies")
    args = ap.parse_args()

    companies = load_config()
    if args.slug:
        companies = [c for c in companies if c["slug"] == args.slug]
    if args.limit:
        companies = companies[: args.limit]

    today = datetime.date.today().isoformat()
    print(f"  TermsGuard corpus snapshot, {today}")
    print(f"  {len(companies)} companies to process")
    print()

    results = []
    t0 = time.time()
    for i, company in enumerate(companies, 1):
        outdir = SNAPSHOTS / company["slug"]
        outdir.mkdir(parents=True, exist_ok=True)
        result = snapshot_one(company, today, outdir)
        results.append(result)
        print(f"  [{i}/{len(companies)}] {result['slug']}: {result.get('reason', 'ok')}")
        print()

    elapsed = time.time() - t0
    ok = sum(1 for r in results if r["ok"])
    print(f"  done: {ok}/{len(results)} ok in {elapsed:.0f}s")

    # Write a run summary at the corpus root for /build.py
    run_summary = {
        "date": today,
        "elapsed_seconds": round(elapsed, 1),
        "ok_count": ok,
        "total_count": len(results),
        "results": results,
    }
    (SNAPSHOTS / "_latest.json").write_text(json.dumps(run_summary, indent=2))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
