#!/usr/bin/env python3
"""
TermsGuard corpus build script.

Reads corpus/snapshots/<slug>/<date>.{txt,json,diff} and emits a static
HTML page at site-build/corpus/index.html.

Two modes:
  --no-llm   skip the LLM-generated analyst prose; fall back to a
             mechanical summary from the clauses. Use this when the
             LLM gateway is rate-limited or you want a pure-deterministic
             build.
  --llm-mode=preview  (default) call the LLM once per company to write
             the analyst summary + overall analysis.

Pure file-I/O otherwise. Re-run after snapshot.py.

Output structure:
  /home/newtazer/site-build/corpus/index.html
"""
import argparse
import datetime
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SNAPSHOTS = HERE / "snapshots"
CONFIG = HERE / "snapshot.yml"

# Output target is the portfolio site, NOT this repo. Path is the single
# source of truth; change here if the portfolio moves.
SITE_ROOT = Path("/home/newtazer/site-build")
SITE_CORPUS = SITE_ROOT / "corpus"


def load_config():
    with CONFIG.open() as f:
        return yaml.safe_load(f)["companies"]


def load_company_snapshots(slug: str) -> list:
    """Return all snapshots for a slug, oldest first, each as a dict.

    Also computes deltas against the previous snapshot:
      - score_delta:    score[i] - score[i-1]
      - flagged_delta:  flagged[i] - flagged[i-1]
      - cleared_delta:  cleared[i] - cleared[i-1]
      - articles_added: set of GDPR bases newly appearing in flagged set
      - articles_removed: set of GDPR bases no longer in flagged set
    These power the "what changed since last run" section.
    """
    out = []
    d = SNAPSHOTS / slug
    if not d.exists():
        return out
    paths = sorted(d.glob("*.json"))
    for p in paths:
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        s = data.get("summary", {})
        flagged_clauses = data.get("non_ethical_points", [])
        cleared_clauses = data.get("ethical_points", [])
        out.append({
            "date": data.get("fetched_at") or p.stem,
            "source": data.get("source", ""),
            "url": data.get("url", ""),
            "risk_level": s.get("risk_level", "Unknown"),
            "score": s.get("score", 0),
            "flagged": s.get("flagged_count", 0),
            "cleared": s.get("cleared_count", 0),
            "uncertain": s.get("uncertain_count", 0),
            "clauses": s.get("clauses_analyzed", 0),
            "summary_text": s.get("summary", ""),
            "changed_lines": s.get("changed_lines", 0),
            "prior": s.get("prior_snapshot"),
            "flagged_clauses": flagged_clauses,
            "cleared_clauses": cleared_clauses,
            "uncertain_clauses": data.get("unclear_points", []),
        })

    # Compute deltas against previous snapshot
    for i in range(1, len(out)):
        prev, cur = out[i - 1], out[i]
        cur["score_delta"] = round(cur["score"] - prev["score"], 3)
        cur["flagged_delta"] = cur["flagged"] - prev["flagged"]
        cur["cleared_delta"] = cur["cleared"] - prev["cleared"]
        # Article sets
        prev_articles = {p.get("gdpr_basis", "").split(" ")[0]
                         for p in prev["flagged_clauses"] if p.get("gdpr_basis")}
        cur_articles = {p.get("gdpr_basis", "").split(" ")[0]
                        for p in cur["flagged_clauses"] if p.get("gdpr_basis")}
        cur["articles_added"] = sorted(cur_articles - prev_articles)
        cur["articles_removed"] = sorted(prev_articles - cur_articles)
        # Same for cleared
        prev_cleared = {p.get("gdpr_basis", "").split(" ")[0]
                         for p in prev["cleared_clauses"] if p.get("gdpr_basis")}
        cur_cleared = {p.get("gdpr_basis", "").split(" ")[0]
                        for p in cur["cleared_clauses"] if p.get("gdpr_basis")}
        cur["cleared_articles_added"] = sorted(cur_cleared - prev_cleared)
        cur["cleared_articles_removed"] = sorted(prev_cleared - cur_cleared)

    # First snapshot has no deltas
    if out:
        out[0].update({
            "score_delta": None, "flagged_delta": None,
            "cleared_delta": None, "articles_added": [], "articles_removed": [],
            "cleared_articles_added": [], "cleared_articles_removed": [],
        })

    return out


def sparkline_data(snapshots: list) -> list:
    """Return just the score history for the sparkline."""
    return [s["score"] for s in snapshots]


def latest_diff_text(slug: str) -> str:
    """Read the most recent .diff file (may be empty)."""
    d = SNAPSHOTS / slug
    diffs = sorted(d.glob("*.diff"))
    if not diffs:
        return ""
    return diffs[-1].read_text()


# Inline JS for sparkline + diff rendering. Loaded once per page.
SPARKLINE_JS = r"""
// sparklineSVG(values) -> string. 200x40, currentColor strokes, n=1 fallback.
function sparklineSVG(values) {
  if (!values || values.length === 0) return "";
  const W = 200, H = 40;
  const n = values.length;
  if (n === 1) {
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}">
      <circle cx="${W/2}" cy="${H/2}" r="2.5" fill="currentColor"/></svg>`;
  }
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const dx = W / (n - 1);
  const points = values.map((v, i) => {
    const x = i * dx;
    const y = H - ((v - min) / range) * H;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const last = values[values.length - 1];
  const lx = (n - 1) * dx;
  const ly = H - ((last - min) / range) * H;
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}">
    <polyline points="${points}" fill="none" stroke="currentColor" stroke-width="1.5"/>
    <circle cx="${lx.toFixed(2)}" cy="${ly.toFixed(2)}" r="2.5" fill="currentColor"/></svg>`;
}
"""

DIFF_JS = r"""
function renderDiff(diffText) {
  const host = document.createElement("div");
  host.className = "diff";
  const lines = diffText.split("\n");
  let oldNo = 0, newNo = 0;
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  for (const raw of lines) {
    if (raw.startsWith("---") || raw.startsWith("+++")) {
      const row = document.createElement("div");
      row.className = "diff-line hunk";
      row.innerHTML = `<span class="diff-gutter" style="visibility:hidden">.</span><span class="diff-content">${esc(raw)}</span>`;
      host.appendChild(row); continue;
    }
    if (raw.startsWith("@@")) {
      const m = raw.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) { oldNo = parseInt(m[1],10) - 1; newNo = parseInt(m[2],10) - 1; }
      const row = document.createElement("div");
      row.className = "diff-line hunk";
      row.innerHTML = `<span class="diff-gutter" style="visibility:hidden">.</span><span class="diff-content">${esc(raw)}</span>`;
      host.appendChild(row); continue;
    }
    const marker = raw[0];
    const content = raw.length > 0 ? raw.slice(1) : "";
    if (marker === "+") {
      newNo++;
      const row = document.createElement("div");
      row.className = "diff-line add";
      row.innerHTML = `<span class="diff-gutter"><span></span><span>${newNo}</span><span class="marker">+</span></span><span class="diff-content">${esc(content)}</span>`;
      host.appendChild(row);
    } else if (marker === "-") {
      oldNo++;
      const row = document.createElement("div");
      row.className = "diff-line remove";
      row.innerHTML = `<span class="diff-gutter"><span>${oldNo}</span><span></span><span class="marker">-</span></span><span class="diff-content">${esc(content)}</span>`;
      host.appendChild(row);
    } else if (marker === " ") {
      oldNo++; newNo++;
      const row = document.createElement("div");
      row.className = "diff-line";
      row.innerHTML = `<span class="diff-gutter"><span>${oldNo}</span><span>${newNo}</span><span class="marker"> </span></span><span class="diff-content">${esc(content)}</span>`;
      host.appendChild(row);
    } else if (raw.length > 0) {
      oldNo++; newNo++;
      const row = document.createElement("div");
      row.className = "diff-line";
      row.innerHTML = `<span class="diff-gutter"><span>${oldNo}</span><span>${newNo}</span><span class="marker"> </span></span><span class="diff-content">${esc(raw)}</span>`;
      host.appendChild(row);
    }
  }
  return host;
}
"""


def html_escape(s):
    """Minimal HTML escape. Treats `s` as a plain string."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def attr_escape(s):
    """HTML escape for href / data-attribute values. Escapes `"` so it doesn't
    terminate the attribute."""
    return html_escape(s).replace("&#39;", "%27")  # `'` is fine inside "..."


# ---------- LLM prose generation --------------------------------------------

def call_llm(prompt: str, max_tokens: int = 600) -> str:
    """Call the LLM gateway to write analyst prose. Returns the raw text.
    Falls back to empty string on any failure (the build keeps going
    with mechanical text instead of failing).

    The ask.py gateway emits several lines of routing metadata before
    the actual model output. We strip everything from the start of the
    response up to (and including) the first '[cache hit]', '[cache miss]',
    or other routing marker that's followed by the actual content. The
    heuristic: take everything AFTER the first occurrence of a routing
    marker followed by a newline. As a safety net, if no marker is
    found, we look for a known prose-pattern marker instead.
    """
    try:
        cmd = [
            "python3", str(Path.home() / ".llm" / "ask.py"),
            "call", "draft", prompt,
            "--model", "gpt-5.6-luna",
            "--max-tokens", str(max_tokens),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"      LLM call failed (rc={result.returncode}): {result.stderr[:200]}")
            return ""
        out = result.stdout

        # Strategy: find the LAST occurrence of a known gateway-marker
        # followed by the actual content. The gateway emits one line per
        # operation; the last such line is the most recent (the actual
        # model call), and what follows it is the response.
        markers = [
            "[cache hit]",
            "[cache miss]",
            "[reasoning burn",
            "[routing",
            "[ledger",
        ]
        # Find the LAST occurrence of any marker
        best_idx = -1
        for m in markers:
            idx = out.rfind(m)
            if idx > best_idx:
                best_idx = idx
        if best_idx > 0:
            # Take everything after the marker line
            # Find the end of that line
            newline_at = out.find("\n", best_idx)
            if newline_at >= 0:
                out = out[newline_at + 1:]
        return out.strip()
    except Exception as e:
        print(f"      LLM call exception: {e}")
        return ""


def summarise_flagged_themes(flagged: list) -> list:
    """Group flagged clauses by their matched GDPR basis. Return
    [(basis_label, count, sample_clause_text), ...] sorted by count desc.
    """
    from collections import Counter
    buckets = {}
    for p in flagged:
        basis = p.get("gdpr_basis", "") or "no basis match"
        buckets.setdefault(basis, []).append(p.get("text", ""))
    out = []
    for basis, texts in sorted(buckets.items(), key=lambda x: -len(x[1])):
        out.append((basis, len(texts), texts[0]))
    return out


def render_methodology_note(snap: dict) -> str:
    """Mechanical methodology paragraph. Honest about what the engine
    actually did for this snapshot, no LLM needed."""
    score = snap.get("score", 0)
    risk = snap.get("risk_level", "Unknown")
    flagged = snap.get("flagged", 0)
    cleared = snap.get("cleared", 0)
    uncertain = snap.get("uncertain", 0)
    clauses = snap.get("clauses", 0)
    flagged_clauses = snap.get("flagged_clauses", [])
    # Most common GDPR basis (the strongest signal in the result)
    themes = summarise_flagged_themes(flagged_clauses)
    top_basis = themes[0][0] if themes else ""
    return f"""<p class="methodology">
      How this was scored: <span class="mono">{clauses}</span> clauses were extracted from the
      policy and matched against the <span class="mono">102</span> hand-labelled reference clauses
      (<span class="mono">52</span> fair, <span class="mono">50</span> unfair). The top match for each
      clause was checked against the
      <span class="mono">550</span>-chunk reference index of the actual GDPR text
      (<span class="mono">99</span> articles + <span class="mono">173</span> recitals), and the matching
      article was attached to the flag. The score is the mean confidence of the
      <span class="mono">{flagged}</span> flagged clauses
      {f', most of them under <em>{html_escape(top_basis)}</em>' if top_basis and top_basis != 'no basis match' else ''}.
      No model verification on this run; cosine + GDPR retrieval only.
    </p>"""


def render_clause_block(p: dict, kind: str) -> str:
    """Render one clause as a card: text, GDPR article chip, retrieved evidence."""
    text = html_escape(p.get("text", ""))
    basis = html_escape(p.get("gdpr_basis", "") or "no basis match")
    evidence = html_escape(p.get("gdpr_evidence", "")[:300])
    confidence = p.get("confidence", 0)
    # Strip "violation" / "non-violation" suffix from basis for display
    basis_short = re.sub(r"\s*(violation|non-violation|compliance)\s*$", "", basis)
    # Strip em/en dashes inside basis labels (they came from the engine
    # but the page is supposed to be dash-free per CLAUDE.md).
    basis_short = basis_short.replace(" — ", " ").replace("–", "-")
    return f"""<div class="clause-block clause-{kind}">
  <p class="clause-text">{text}</p>
  <p class="clause-meta">
    <span class="law-chip">{basis_short}</span>
    <span class="clause-confidence">confidence {confidence:.2f}</span>
  </p>
  <p class="clause-evidence"><em>Retrieved context:</em> {evidence}...</p>
</div>"""


def render_clauses_section(flagged: list, cleared: list) -> str:
    """Top 3-5 of each, by confidence. Honest about which clauses matter."""
    top_f = sorted(flagged, key=lambda p: -p.get("confidence", 0))[:4]
    top_c = sorted(cleared, key=lambda p: -p.get("confidence", 0))[:4]
    flagged_html = "\n".join(render_clause_block(p, "bad") for p in top_f) if top_f else \
        '<p class="empty-state">No clauses were flagged.</p>'
    cleared_html = "\n".join(render_clause_block(p, "good") for p in top_c) if top_c else \
        '<p class="empty-state">No clauses were cleared.</p>'
    return f"""
<section class="clauses">
  <h4>Flagged clauses · what TermsGuard thinks you should read</h4>
  <p class="clauses-meta">Top {len(top_f)} of {len(flagged)} by confidence. Each clause shows the matched GDPR article and the passage the engine used.</p>
  {flagged_html}
</section>
<section class="clauses">
  <h4>Cleared clauses · what TermsGuard reads as protective</h4>
  <p class="clauses-meta">Top {len(top_c)} of {len(cleared)} by confidence. These are the clauses the engine read as GDPR-compliant.</p>
  {cleared_html}
</section>"""


def render_notes(snap: dict) -> str:
    """Mechanical notes paragraph. Lists what the engine couldn't see or didn't do."""
    flagged_clauses = snap.get("flagged_clauses", [])
    cleared_clauses = snap.get("cleared_clauses", [])
    uncertain_clauses = snap.get("uncertain_clauses", [])
    total = len(flagged_clauses) + len(cleared_clauses) + len(uncertain_clauses)
    clauses = snap.get("clauses", 0)
    notes = []
    if total < clauses:
        notes.append(
            f"{clauses - total} clauses were not classified (no high-confidence match in either direction)."
        )
    if uncertain_clauses:
        notes.append(
            f"{len(uncertain_clauses)} clauses were left uncertain. They sit between the fair and unfair reference sets and the engine did not commit."
        )
    if not flagged_clauses:
        notes.append("No clauses were flagged. The score is the mean confidence of the cleared-clause set; treat as a low-noise baseline.")
    if not cleared_clauses:
        notes.append("No clauses were cleared. The corpus has no clauses that read as GDPR-compliant by the reference comparison.")
    notes.append(
        "The verification model is not configured for this corpus run. Cosine similarity + GDPR-article retrieval are the only signals."
    )
    notes.append(
        "This is a measurement of the published policy text, not the company's actual data-handling practice. The policy can be more or less protective than the practice."
    )
    return '<ul class="notes-list">' + "\n".join(f"  <li>{html_escape(n)}</li>" for n in notes) + "\n</ul>"


def mechanical_analyst_summary(snap: dict) -> str:
    """Mechanical one-paragraph analyst read. Used when --no-llm is set
    or the LLM call fails. Honest but mechanical."""
    themes = summarise_flagged_themes(snap.get("flagged_clauses", []))
    top3 = themes[:3]
    flagged = snap.get("flagged", 0)
    cleared = snap.get("cleared", 0)
    risk = snap.get("risk_level", "Unknown")
    score = snap.get("score", 0)
    if not top3:
        return f"TermsGuard's reading: this policy scores {risk.lower()} risk. No clauses were flagged; the {cleared} cleared clauses hold the score down to {score:.2f}."
    parts = [f"TermsGuard's reading: {risk.lower()} risk with score {score:.2f}."]
    parts.append(
        f"The {flagged} flagged clauses cluster under "
        + ", ".join(f"<em>{html_escape(b)}</em> ({c})" for b, c, _ in top3)
        + "."
    )
    if cleared > 0:
        parts.append(
            f"The {cleared} cleared clauses show the policy is willing to commit to specific user-facing protections; whether those commitments match actual data practice is not something this measurement can show."
        )
    return " ".join(parts)


def mechanical_overall_analysis(snap: dict) -> str:
    """Mechanical synthesis paragraph. Used as the overall-analysis fallback."""
    themes = summarise_flagged_themes(snap.get("flagged_clauses", []))
    flagged = snap.get("flagged", 0)
    cleared = snap.get("cleared", 0)
    risk = snap.get("risk_level", "Unknown")
    if not themes:
        return f"On the whole: the engine reads this policy as {risk.lower()} risk, with the {cleared} cleared clauses doing the work of pulling the score down. The flagged-clause set is empty; there are no specific GDPR articles this policy appears to violate by the engine's reading."
    top_basis = themes[0][0]
    return f"On the whole: the engine reads this policy as {risk.lower()} risk. The dominant concern is around <em>{html_escape(top_basis)}</em>, with {themes[0][1]} of the {flagged} flagged clauses sitting under that article. The cleared-clause set ({cleared}) shows the policy is willing to commit to user-facing protections in adjacent areas. The shape of the policy is consistent with a corporate privacy notice written for GDPR compliance, not a policy designed from a user-rights-first perspective; the flagged clauses reflect the gaps."

# ---------- Change indicators + "What changed since last run" -----------------

def render_change_indicator(snap: dict) -> str:
    """Returns inner HTML (no <p> wrapper) for the change indicator line.
    The card template wraps this in a <p class="latest-meta">.
    """
    if snap.get("score_delta") is None:
        # First snapshot
        return (f'Last fetched <span class="mono">{snap["date"]}</span>'
                f' · first snapshot')
    sd = snap["score_delta"]
    fd = snap["flagged_delta"]
    cd = snap["cleared_delta"]
    sd_str = f"{sd:+.3f}" if sd >= 0 else f"{sd:.3f}"
    fd_str = f"+{fd}" if fd >= 0 else f"{fd}"
    cd_str = f"+{cd}" if cd >= 0 else f"{cd}"
    sd_class = "delta-up" if sd > 0 else ("delta-down" if sd < 0 else "delta-flat")
    fd_class = "delta-up" if fd > 0 else ("delta-down" if fd < 0 else "delta-flat")
    cd_class = "delta-up" if cd > 0 else ("delta-down" if cd < 0 else "delta-flat")
    return (f'Last fetched <span class="mono">{snap["date"]}</span>'
            f' · score <span class="delta {sd_class}">{sd_str}</span>'
            f' · flagged <span class="delta {fd_class}">{fd_str}</span>'
            f' · cleared <span class="delta {cd_class}">{cd_str}</span>'
            f' · {snap["changed_lines"]} lines changed')


def render_what_changed(snap: dict) -> str:
    """Mechanical "What changed since last run" subsection for 2+-snapshot
    companies. Computes deltas vs the previous snapshot. For 1-snapshot
    companies, returns nothing.
    """
    if snap.get("score_delta") is None:
        return ""

    sd = snap["score_delta"]
    fd = snap["flagged_delta"]
    cd = snap["cleared_delta"]
    added = snap.get("articles_added", [])
    removed = snap.get("articles_removed", [])
    cl_added = snap.get("cleared_articles_added", [])
    cl_removed = snap.get("cleared_articles_removed", [])

    # Line 1: score movement with delta narrative
    if abs(sd) < 0.01:
        score_line = (
            f"Score is unchanged at <span class=\"mono\">{snap['score']:.2f}</span>. "
            f"The flagged-clause count moved by {fd:+d}, the cleared-clause count by {cd:+d}; the two changes cancelled in the score mean."
        )
    elif sd > 0:
        score_line = (
            f"Score moved from <span class=\"mono\">{snap['score'] - sd:.2f}</span> to <span class=\"mono\">{snap['score']:.2f}</span> "
            f"(<span class=\"delta delta-up\">+{sd:.3f}</span>). "
            f"Flagged-clause count moved by {fd:+d}, cleared-clause count by {cd:+d}."
        )
    else:
        score_line = (
            f"Score moved from <span class=\"mono\">{snap['score'] - sd:.2f}</span> to <span class=\"mono\">{snap['score']:.2f}</span> "
            f"(<span class=\"delta delta-down\">{sd:.3f}</span>). "
            f"Flagged-clause count moved by {fd:+d}, cleared-clause count by {cd:+d}."
        )

    # Line 2: articles added/removed in the flagged set
    article_line_parts = []
    if added:
        article_line_parts.append(
            f"GDPR articles newly appearing in the flagged set: {', '.join(html_escape(a) for a in added[:5])}"
        )
    if removed:
        article_line_parts.append(
            f"GDPR articles no longer in the flagged set: {', '.join(html_escape(a) for a in removed[:5])}"
        )
    if not added and not removed:
        article_line_parts.append("The set of GDPR articles in the flagged clauses did not change.")
    article_line = " ".join(article_line_parts)

    # Line 3: cleared set changes
    cleared_line_parts = []
    if cl_added:
        cleared_line_parts.append(
            f"The cleared-clause set gained new articles: {', '.join(html_escape(a) for a in cl_added[:5])}"
        )
    if cl_removed:
        cleared_line_parts.append(
            f"The cleared-clause set lost: {', '.join(html_escape(a) for a in cl_removed[:5])}"
        )
    if not cl_added and not cl_removed:
        cleared_line_parts.append("The cleared-clause article set is unchanged.")
    cleared_line = " ".join(cleared_line_parts)

    # Verdict
    if sd > 0.01:
        verdict = "Verdict: this week is a slight tightening of the engine's reading, not a policy overhaul."
    elif sd < -0.01:
        verdict = "Verdict: this week is a slight loosening of the engine's reading, not a policy overhaul."
    elif fd > 0 or cd < 0:
        verdict = "Verdict: the score did not move, but the flagged set grew. The engine is reading more clauses as concerning than before, just with similar confidence."
    else:
        verdict = "Verdict: this week's change is within the engine's normal noise band. No material movement."

    return f"""<section class="what-changed">
    <h4 class="section-h">What changed since last run</h4>
    <p>{score_line}</p>
    <p>{article_line}</p>
    <p>{cleared_line}</p>
    <p>{verdict}</p>
  </section>"""


# ---------- Top-of-page "How to read this" callout --------------------------

HOW_TO_READ_CALLOUT = """<section class="how-to-read">
  <h2 class="section-h">How to read this</h2>
  <p>
    The score is the mean cosine confidence of the flagged clauses, range 0 to 1.
    Risk labels (Low / Medium / High / Unknown) come from fixed score bands, not a learned model.
    A diff is computed between the current snapshot and the previous one for the same company;
    companies with only one snapshot have no diff to show.
  </p>
  <p class="legend">
    <span class="legend-item"><span class="legend-swatch swatch-flagged"></span> Flagged = matches the unfair reference set</span>
    <span class="legend-item"><span class="legend-swatch swatch-cleared"></span> Cleared = matches the fair reference set</span>
    <span class="legend-item"><span class="legend-swatch swatch-uncertain"></span> Uncertain = no confident match</span>
  </p>
</section>"""


HOW_TO_READ_FOOTER = """<section class="how-to-read-footer">
  <h2 class="section-h">How to read this</h2>
  <p>
    The score is a measurement of one document at one point in time, not a verdict.
    Treat it like a temperature reading: useful as a signal, not a diagnosis.
    The flagged-clause list is the engine's best read of where a privacy-aware reader would push back, not a legal determination.
    The cleared-clause list is the engine's best read of where the policy is committing to user protection.
    The diff between two snapshots is a literal text diff; the prose around it is the engine's interpretation of what changed.
    None of this replaces legal review.
  </p>
</section>"""

# ---------- LLM-generated analyst prose ------------------------------------

ANALYST_PROMPT_TEMPLATE = """You are writing a short analyst note for a TermsGuard corpus page. The reader is a recruiter or a privacy lawyer. They want to know what the engine read in this policy and why it scored the way it did.

# Hard constraints
- You may ONLY describe what is in the input below. Do not invent claims, companies, dates, or article numbers.
- Do not use marketing language ("robust", "leverage", "world-class", "seamless", "cutting-edge").
- Do not use the phrases "I think" or "in my opinion". State the read.
- No em dashes. No en dashes. Use a period and a new sentence instead, or " and ".
- Length: 2-4 sentences for the analyst summary, 3-5 for the overall analysis.

# Voice
Short declarative sentences. Two-part contrasts ("the flagged clauses do X; the cleared clauses do Y"). First-person plural ("TermsGuard reads", "we read") is acceptable; first-person singular ("I think") is not.

# Input

Company: {company_name}
Policy fetched: {date}
Score: {score} (mean confidence of flagged clauses)
Risk level: {risk_level}
Total clauses analysed: {clauses}
Flagged clauses: {flagged_count}
Cleared clauses: {cleared_count}

Top flagged clauses (by confidence):
{flagged_clause_texts}

Top cleared clauses (by confidence):
{cleared_clause_texts}

Most common GDPR bases in the flagged set:
{theme_summary}

# Output

Produce TWO paragraphs, separated by a blank line.

Paragraph 1 (analyst summary, 2-4 sentences):
- Open with "TermsGuard's reading:".
- Name what KIND of risk this policy presents (not just the level). What kind of behaviour does the engine see?
- Reference the dominant GDPR articles by name (e.g., "Article 6 Lawfulness of processing").
- Do not flatter. State what the engine saw.

Paragraph 2 (overall analysis, 3-5 sentences):
- Connect the flagged and cleared lists. What does the shape of the policy tell a reader?
- Do not invent. If the cleared set is empty, say so.
- No call to action. No "we recommend".

Output ONLY the two paragraphs. No headings. No bullet points. No "Note:" preambles.
"""


def call_llm_analyst(snap: dict, company: dict) -> tuple:
    """Call the LLM to write the analyst summary + overall analysis.
    Returns (analyst_summary_html, overall_analysis_html) — already
    paragraph-wrapped in <p> tags. Empty strings on failure.

    The prose is constrained to only describe clauses in the input.
    A safety check rejects any output that contains phrases like
    'leverage', 'world-class', 'seamless', 'cutting-edge', or any
    sentence containing an em or en dash.
    """
    flagged = snap.get("flagged_clauses", [])[:4]
    cleared = snap.get("cleared_clauses", [])[:4]

    def clause_lines(cs):
        out = []
        for p in cs:
            t = p.get("text", "")[:300]
            b = p.get("gdpr_basis", "")
            out.append(f"  - [{b}] {t}")
        return "\n".join(out) if out else "  (none)"

    themes = summarise_flagged_themes(snap.get("flagged_clauses", []))[:5]
    theme_lines = "\n".join(f"  - {b}: {c} clauses" for b, c, _ in themes)

    prompt = ANALYST_PROMPT_TEMPLATE.format(
        company_name=company["name"],
        date=snap["date"],
        score=f"{snap.get('score', 0):.2f}",
        risk_level=snap.get("risk_level", "Unknown"),
        clauses=snap.get("clauses", 0),
        flagged_count=snap.get("flagged", 0),
        cleared_count=snap.get("cleared", 0),
        flagged_clause_texts=clause_lines(flagged),
        cleared_clause_texts=clause_lines(cleared),
        theme_summary=theme_lines,
    )

    out = call_llm(prompt, max_tokens=700)
    if not out:
        return "", ""

    # Safety: reject em/en dashes and banned words
    banned = ["leverage", "world-class", "seamless", "cutting-edge", "robust",
              "holistic", "unlock", "revolutionary", "game-changing"]
    for word in banned:
        if re.search(rf"\b{re.escape(word)}\b", out, re.IGNORECASE):
            print(f"      rejected LLM output (contains '{word}')")
            return "", ""
    if "—" in out or "–" in out:
        print(f"      rejected LLM output (contains em/en dash)")
        return "", ""

    # Split on blank line into two paragraphs
    parts = re.split(r"\n\s*\n", out.strip(), maxsplit=1)
    if len(parts) >= 2:
        summary_html = f"<p>{html_escape(parts[0].strip())}</p>"
        overall_html = f"<p>{html_escape(parts[1].strip())}</p>"
    else:
        summary_html = f"<p>{html_escape(out.strip())}</p>"
        overall_html = ""
    return summary_html, overall_html


def render_company_card(company: dict, snapshots: list, llm_mode: str = "preview") -> str:
    """One card per company: header, metrics, analyst summary, methodology,
    flagged + cleared clause blocks, overall analysis, notes, latest diff."""
    slug = company["slug"]
    name = html_escape(company["name"])
    sector = html_escape(company["sector"])
    url = attr_escape(company["url"])
    if not snapshots:
        return f"""
<article class="company" data-slug="{slug}" data-sector="{sector}">
  <header>
    <h3>{name} <span class="sector">{sector}</span></h3>
    <p class="fetch-url"><a href="{url}" target="_blank" rel="noopener">policy source</a></p>
  </header>
  <p class="empty-state">No snapshot yet.</p>
</article>
"""
    latest = snapshots[-1]
    history = sparkline_data(snapshots)
    history_json = json.dumps(history)
    diff_text = latest_diff_text(slug)
    diff_block = (
        f'<div class="diff-host" data-diff-text="{html_escape(diff_text[:8000])}"></div>'
        if diff_text.strip() else '<p class="empty-state">No diff yet (first snapshot).</p>'
    )

    # Analyst prose: LLM-driven if available, mechanical fallback if not.
    if llm_mode == "no-llm":
        analyst_html = f"<p>{html_escape(mechanical_analyst_summary(latest))}</p>"
        overall_html = f"<p>{html_escape(mechanical_overall_analysis(latest))}</p>"
    else:
        analyst_html, overall_html = call_llm_analyst(latest, company)
        if not analyst_html:
            print(f"  [{slug}] LLM failed; using mechanical analyst summary")
            analyst_html = f"<p>{html_escape(mechanical_analyst_summary(latest))}</p>"
        if not overall_html:
            overall_html = f"<p>{html_escape(mechanical_overall_analysis(latest))}</p>"

    methodology_html = render_methodology_note(latest)
    clauses_html = render_clauses_section(
        latest.get("flagged_clauses", []),
        latest.get("cleared_clauses", []),
    )
    notes_html = render_notes(latest)
    change_indicator_html = render_change_indicator(latest)
    what_changed_html = render_what_changed(latest)

    return f"""
<article class="company" data-slug="{slug}" data-sector="{sector}" data-risk="{latest['risk_level']}">
  <header>
    <h3>{name} <span class="sector">{sector}</span></h3>
    <p class="fetch-url"><a href="{url}" target="_blank" rel="noopener">policy source</a></p>
  </header>
  <div class="metrics">
    <div class="metric">
      <span class="metric-num">{latest['score']:.2f}</span>
      <span class="metric-lab">score</span>
    </div>
    <div class="metric">
      <span class="metric-num risk-{latest['risk_level']}">{latest['risk_level']}</span>
      <span class="metric-lab">risk</span>
    </div>
    <div class="metric">
      <span class="metric-num">{latest['flagged']}</span>
      <span class="metric-lab">flagged</span>
    </div>
    <div class="metric">
      <span class="metric-num">{latest['cleared']}</span>
      <span class="metric-lab">cleared</span>
    </div>
    <div class="metric sparkline-cell">
      <span class="sparkline" data-history='{history_json}'></span>
      <span class="metric-lab">{len(snapshots)} snapshot{'s' if len(snapshots) != 1 else ''}</span>
    </div>
  </div>
  <p class="latest-meta">
    {change_indicator_html}
  </p>

  <section class="analyst">
    <h4 class="section-h">Analyst summary</h4>
    {analyst_html}
  </section>

  <section class="method-block">
    <h4 class="section-h">How this was scored</h4>
    {methodology_html}
  </section>

  <section class="clauses-block">
    {clauses_html}
  </section>

  <section class="overall">
    <h4 class="section-h">Overall analysis</h4>
    {overall_html}
  </section>

  {what_changed_html}

  <section class="notes-block">
    <h4 class="section-h">Notes and caveats</h4>
    {notes_html}
  </section>

  <div class="diff-section">
    <p class="diff-section-label">Latest diff</p>
    {diff_block}
  </div>
</article>
"""


def render_score_table(companies: list, snapshots_by_slug: dict) -> str:
    """Sortable score table at the top of the page."""
    rows = []
    for c in companies:
        snaps = snapshots_by_slug.get(c["slug"], [])
        if not snaps:
            continue
        latest = snaps[-1]
        prior = snaps[-2] if len(snaps) > 1 else None
        risk_cls = f"risk-{latest['risk_level'].lower()}"
        change = latest["changed_lines"]
        if change > 0:
            change_cell = f'<span class="change grew">+{change}</span>'
        elif change < 0:
            change_cell = f'<span class="change shrank">{change}</span>'
        else:
            change_cell = '<span class="change none">.</span>'
        rows.append(f"""
<tr class="corpus-row" data-risk="{latest['risk_level']}" data-slug="{c['slug']}">
  <td class="corpus-name">
    <a href="{attr_escape(c['url'])}" target="_blank" rel="noopener">{html_escape(c['name'])}</a>
    <span class="corpus-sector">{html_escape(c['sector'])}</span>
  </td>
  <td class="corpus-risk {risk_cls}">{latest['risk_level']}</td>
  <td class="corpus-num">{latest['score']:.2f}</td>
  <td class="corpus-num">{latest['flagged']}</td>
  <td class="corpus-num">{latest['cleared']}</td>
  <td class="corpus-num">{latest['uncertain']}</td>
  <td class="corpus-date">{latest['date']}</td>
  <td class="corpus-date">{prior['date'] if prior else '·'}</td>
  <td>{change_cell}</td>
</tr>
""")
    return "\n".join(rows)


PAGE_CSS = """
/* TermsGuard corpus page-specific styles. Reuses design-system.css tokens. */
.corpus-page .stamp {
  font-family: var(--font-mono); font-size: var(--text-xs);
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-3);
  padding-bottom: var(--space-3); border-bottom: 1px solid var(--border);
  margin-bottom: var(--space-5);
}
.corpus-page .lede {
  margin-top: var(--space-4); max-width: var(--content-max);
  color: var(--text-2); font-size: var(--text-base); line-height: 1.6;
}
.corpus-page h2 {
  font-size: var(--text-lg); margin-top: var(--space-12);
  margin-bottom: var(--space-4); font-weight: 500;
  letter-spacing: -0.01em;
}
.corpus-page .sampling {
  margin-top: var(--space-4); padding: var(--space-3) var(--space-4);
  border-left: 2px solid var(--accent); background: var(--bg-2);
  font-size: var(--text-sm); color: var(--text-2);
  max-width: var(--content-max);
}

/* Score table */
.corpus-table { width: 100%; border-collapse: collapse; margin-top: var(--space-4);
  font-size: var(--text-sm); }
.corpus-table th {
  font-family: var(--font-mono); font-size: var(--text-xs);
  text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-3);
  text-align: left; padding: var(--space-3); border-bottom: 1px solid var(--border);
  font-weight: 400;
}
.corpus-table td { padding: var(--space-3); border-bottom: 1px solid var(--border-2);
  vertical-align: top; }
.corpus-table .corpus-name a { color: var(--text); text-decoration: none;
  border-bottom: 1px solid transparent; font-weight: 500; }
.corpus-table .corpus-name a:hover { color: var(--accent);
  border-bottom-color: var(--accent); }
.corpus-table .corpus-sector {
  display: block; font-size: var(--text-xs); color: var(--text-3);
  margin-top: 2px;
}
.corpus-table .corpus-num, .corpus-table .corpus-date { font-family: var(--font-mono);
  font-size: var(--text-xs); color: var(--text-2); white-space: nowrap; }
.corpus-table .corpus-risk { font-family: var(--font-mono); font-size: var(--text-xs);
  letter-spacing: 0.05em; text-transform: uppercase; font-weight: 500; }
.corpus-table .risk-high { color: var(--accent); }
.corpus-table .risk-medium { color: var(--text); }
.corpus-table .risk-low { color: var(--text-3); }
.corpus-table .change { font-family: var(--font-mono); font-size: var(--text-xs); }
.corpus-table .change.grew { color: var(--accent); }
.corpus-table .change.shrank { color: var(--text-2); }
.corpus-table .change.none { color: var(--text-3); }

/* Per-company cards */
.company {
  margin-top: var(--space-10); padding-top: var(--space-6);
  border-top: 1px solid var(--border);
}
.company header { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-3); }
.company h3 { font-size: var(--text-lg); font-weight: 500; margin: 0; }
.company .sector {
  font-family: var(--font-mono); font-size: var(--text-xs);
  text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-3);
}
.company .fetch-url { width: 100%; margin: 0; font-size: var(--text-xs); }
.company .fetch-url a { color: var(--text-3); text-decoration: none;
  border-bottom: 1px solid var(--border-2); }
.company .fetch-url a:hover { color: var(--accent); border-bottom-color: var(--accent); }
.company .metrics {
  display: grid; grid-template-columns: repeat(4, auto) 1fr;
  gap: var(--space-5); margin-top: var(--space-4); align-items: end;
}
.company .metric { display: flex; flex-direction: column; gap: 2px; }
.company .metric-num { font-family: var(--font-mono); font-size: var(--text-xl);
  font-weight: 500; color: var(--text); }
.company .metric-num.risk-High { color: var(--accent); }
.company .metric-num.risk-Medium { color: var(--text); }
.company .metric-num.risk-Low { color: var(--text-3); }
.company .metric-lab { font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--text-3); letter-spacing: 0.04em; }
.sparkline-cell { max-width: 220px; }
.sparkline { color: var(--accent); display: block; width: 100%; }
.sparkline svg { display: block; width: 100%; height: 40px; }
.company .latest-meta { margin-top: var(--space-3); font-size: var(--text-sm);
  color: var(--text-2); }
.company .mono { font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--text); }
.company details { margin-top: var(--space-3); }
.company summary { font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--text-3); cursor: pointer; user-select: none;
  letter-spacing: 0.04em; text-transform: uppercase; }
.company details[open] summary { color: var(--accent); }
.diff-section { margin-top: var(--space-3); }
.diff-section-label { font-family: var(--font-mono); font-size: var(--text-xs);
  letter-spacing: 0.04em; text-transform: uppercase;
  color: var(--text-3); margin-bottom: var(--space-2); }
.company .empty-state { color: var(--text-3); font-size: var(--text-sm);
  font-style: italic; }

/* Per-company analyst sections */
.section-h { font-family: var(--font-mono); font-size: var(--text-xs);
  letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--text-3); margin: var(--space-6) 0 var(--space-3); }
.analyst p, .overall p, .method-block p {
  font-size: var(--text-base); color: var(--text); line-height: 1.65;
  max-width: 70ch; margin: 0 0 var(--space-3); }
.method-block p em { font-style: normal; color: var(--accent); font-weight: 500; }

/* Clause blocks */
.clauses-block { margin-top: var(--space-6); }
.clauses { margin-bottom: var(--space-5); }
.clauses h4 { font-size: var(--text-base); font-weight: 500; color: var(--text);
  margin: 0 0 var(--space-2); }
.clauses-meta { font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--text-3); margin: 0 0 var(--space-3); }
.clause-block { padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-3); border-radius: 0 4px 4px 0; }
.clause-block.clause-bad { border-left: 3px solid var(--accent);
  background: var(--accent-bg); }
.clause-block.clause-good { border-left: 3px solid var(--border-2);
  background: var(--bg-2); }
.clause-text { font-size: var(--text-sm); color: var(--text);
  line-height: 1.6; margin: 0 0 var(--space-2); }
.clause-meta { font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--text-3); margin: 0 0 var(--space-2);
  display: flex; flex-wrap: wrap; gap: var(--space-3); align-items: center; }
.law-chip { background: var(--bg); border: 1px solid var(--border);
  padding: 1px 8px; border-radius: 999px; font-size: var(--text-xs);
  color: var(--text-2); }
.clause-bad .law-chip { border-color: var(--accent); color: var(--accent); }
.clause-confidence { color: var(--text-3); }
.clause-evidence { font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--text-3); line-height: 1.5; margin: 0;
  border-top: 1px solid var(--border-2); padding-top: var(--space-2); }
.clause-evidence em { font-style: normal; color: var(--text-2); }

/* Notes block */
.notes-list { margin: 0; padding-left: var(--space-4); font-size: var(--text-sm);
  color: var(--text-2); line-height: 1.6; max-width: 70ch; }
.notes-list li { margin-bottom: var(--space-2); }

/* "How to read" callouts */
.how-to-read { margin-top: var(--space-8); padding: var(--space-5);
  background: var(--bg-2); border: 1px solid var(--border); border-radius: 4px; }
.how-to-read h2 { font-size: var(--text-lg); margin: 0 0 var(--space-3);
  font-weight: 500; letter-spacing: -0.01em; }
.how-to-read p { font-size: var(--text-sm); color: var(--text-2);
  line-height: 1.6; margin: 0 0 var(--space-3); max-width: 70ch; }
.how-to-read .legend { display: flex; gap: var(--space-5); flex-wrap: wrap;
  font-size: var(--text-xs); font-family: var(--font-mono);
  color: var(--text-3); margin-top: var(--space-3); }
.legend-item { display: inline-flex; align-items: center; gap: var(--space-2); }
.legend-swatch { width: 12px; height: 12px; border-radius: 2px; display: inline-block; }
.swatch-flagged { background: var(--accent); }
.swatch-cleared { background: var(--border-2); border: 1px solid var(--border); }
.swatch-uncertain { background: transparent; border: 1px dashed var(--text-3); }

/* Change indicator */
.latest-meta { font-size: var(--text-sm); color: var(--text-2);
  margin: var(--space-3) 0 var(--space-2); }
.delta { font-family: var(--font-mono); font-weight: 700; padding: 1px 5px;
  border-radius: 3px; font-size: var(--text-xs); }
.delta-up { color: var(--accent); background: var(--accent-bg); }
.delta-down { color: var(--live); background: var(--live-bg); }
.delta-flat { color: var(--text-3); }

/* "What changed" subsection */
.what-changed { margin-top: var(--space-6); }
.what-changed p { font-size: var(--text-sm); color: var(--text-2);
  line-height: 1.6; margin: 0 0 var(--space-3); max-width: 70ch; }

/* Footer how-to-read */
.how-to-read-footer { margin-top: var(--space-12); padding-top: var(--space-6);
  border-top: 1px solid var(--border); }
.how-to-read-footer h2 { font-size: var(--text-lg); margin: 0 0 var(--space-3);
  font-weight: 500; letter-spacing: -0.01em; }
.how-to-read-footer p { font-size: var(--text-sm); color: var(--text-2);
  line-height: 1.65; margin: 0; max-width: 70ch; }

/* Diff renderer */
.diff {
  margin-top: var(--space-3); border: 1px solid var(--border);
  font-family: var(--font-mono); font-size: 12px;
  max-height: 500px; overflow-y: auto;
  background: var(--bg);
}
.diff-line { display: flex; align-items: flex-start; line-height: 1.5; }
.diff-line.add { background: rgba(0, 128, 0, 0.06); }
.diff-line.remove { background: rgba(200, 0, 0, 0.06); }
.diff-line.hunk { background: rgba(0,0,0,0.04); color: var(--text-3); }
.diff-gutter { flex: 0 0 7rem; display: flex; justify-content: flex-end;
  gap: 0.25rem; padding: 0 0.5rem; color: var(--text-3);
  user-select: none; border-right: 1px solid var(--border); }
.diff-line.add .diff-gutter { color: var(--live); }
.diff-line.remove .diff-gutter { color: var(--error); }
.diff-gutter .marker { font-weight: 700; width: 1ch; text-align: center; }
.diff-content { flex: 1; padding: 0 0.5rem; white-space: pre-wrap;
  word-break: break-word; }
.empty-state { color: var(--text-3); font-style: italic; font-size: var(--text-sm); }

@media (max-width: 700px) {
  .company .metrics { grid-template-columns: repeat(2, auto); }
  .sparkline-cell { max-width: none; grid-column: 1 / -1; }
}
"""


def render_page(companies: list, snapshots_by_slug: dict, run_info: dict,
                llm_mode: str = "preview", only_slug: str = None) -> str:
    today = datetime.date.today().isoformat()
    ok_count = sum(1 for v in snapshots_by_slug.values() if v)
    fail_count = len(companies) - ok_count
    table_rows = render_score_table(companies, snapshots_by_slug)
    cards_parts = []
    for c in companies:
        if only_slug and c["slug"] != only_slug:
            continue
        cards_parts.append(render_company_card(
            c, snapshots_by_slug.get(c["slug"], []), llm_mode=llm_mode))
    cards = "\n".join(cards_parts)

    # Build the "how to read" callout once, shared across the page.
    how_to_read_callout = HOW_TO_READ_CALLOUT
    how_to_read_footer = HOW_TO_READ_FOOTER
    js_init = """
(function() {
  function init() {
    // sparklines
    document.querySelectorAll(".sparkline").forEach(el => {
      try {
        const data = JSON.parse(el.dataset.history);
        el.innerHTML = sparklineSVG(data);
      } catch (e) {}
    });
    // diffs
    document.querySelectorAll(".diff-host").forEach(el => {
      try {
        const text = el.dataset.diffText || "";
        if (text) el.appendChild(renderDiff(text));
        else el.outerHTML = '<p class="empty-state">Empty diff.</p>';
      } catch (e) {}
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta property="og:title" content="TermsGuard corpus · EU + Big Tech privacy tracker" />
<meta property="og:description" content="A weekly snapshot of TermsGuard scores for 14 EU + Big Tech companies' privacy policies. Built by Taiwo Alabi." />
<meta property="og:type" content="website" />
<title>TermsGuard corpus | Taiwo Alabi</title>
<link rel="stylesheet" href="/assets/css/design-system.css">
<style>{PAGE_CSS}</style>
</head>
<body class="corpus-page">
<div class="wrap">
  <header>
    <p class="stamp">TAIWO ALABI · MSC ARTIFICIAL INTELLIGENCE, NCI DUBLIN</p>
    <h1>TermsGuard corpus</h1>
    <p class="lede">
      The TermsGuard engine pointed at a real, ongoing data-collection problem.
      Each company's privacy policy is fetched on the schedule below, scored by
      the same GDPR-cosine pipeline that powers the live demo, and stored
      versioned with the diff between snapshots. The corpus is the proof
      that the engine works on documents it has never seen before.
    </p>
    <div class="sampling">
      <strong>Sampling frame.</strong> The corpus covers {len(companies)} companies that are
      either (a) the largest data processors operating under GDPR scope,
      or (b) among the top five in their EU category. The list is the
      sample, not a ranking. Each entry justifies itself by size or
      category, not by score. {ok_count} of {len(companies)} fetched successfully this run;
      {fail_count} were rate-limited or bot-blocked and will retry next run.
    </div>
  </header>

  {how_to_read_callout}

  <section>
    <h2>Latest scores</h2>
    <p style="color: var(--text-3); font-size: var(--text-sm); max-width: var(--content-max);">
      All {ok_count} companies with a successful fetch. Score is the mean cosine
      confidence of the policy's flagged (non-ethical) clauses, range
      0-1. Higher means more concerning clauses with higher model
      confidence. Last fetched {today}.
    </p>
    <table class="corpus-table">
      <thead>
        <tr>
          <th>Company</th>
          <th>Risk</th>
          <th>Score</th>
          <th>Flagged</th>
          <th>Cleared</th>
          <th>Uncertain</th>
          <th>Last fetched</th>
          <th>Prior</th>
          <th>Change</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </section>

  <section>
    <h2>Per-company detail</h2>
    {cards}
  </section>

  <section>
    <h2>What this is and is not</h2>
    <p style="max-width: var(--content-max); color: var(--text-2); font-size: var(--text-base); line-height: 1.6;">
      This page is a record of TermsGuard's measurement of the consumer-facing
      privacy policy each company publishes. It is not a legal audit. It is
      not a comparison (each company's score is independent). It is not a
      guarantee of freshness. Companies change their policies without
      notice. The numbers reflect the policy as it was on the date shown.
    </p>
    <p style="max-width: var(--content-max); color: var(--text-2); font-size: var(--text-base); line-height: 1.6;">
      The corpus is fetched on a weekly cadence via GitHub Actions.
      Source: <a href="https://github.com/iamtamilore/terms-guard-ai" target="_blank" rel="noopener">terms-guard-ai</a>
      on GitHub. The live demo this corpus powers:
      <a href="https://newtazer-terms-guard-ai.hf.space" target="_blank" rel="noopener">newtazer-terms-guard-ai.hf.space</a>.
    </p>
  </section>

  {how_to_read_footer}

  <footer style="margin-top: var(--space-12); padding-top: var(--space-5); border-top: 1px solid var(--border);
                 color: var(--text-3); font-size: var(--text-xs);">
    Built by <a href="/" style="color: var(--text-2); text-decoration: none; border-bottom: 1px solid var(--border);">Taiwo Alabi</a>
    · <a href="/cv/" style="color: var(--text-2); text-decoration: none; border-bottom: 1px solid var(--border);">CV</a>
    · <a href="/p/termsguard/" style="color: var(--text-2); text-decoration: none; border-bottom: 1px solid var(--border);">TermsGuard write-up</a>
    · Last run: {today} ({run_info.get('ok_count', 0)}/{run_info.get('total_count', 0)} ok)
  </footer>
</div>

<script>
{SPARKLINE_JS}
{DIFF_JS}
{js_init}
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the rendered page to stdout instead of writing")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip the LLM-generated analyst prose; use mechanical text")
    ap.add_argument("--only-slug", help="only rebuild the card for this slug (debug)")
    args = ap.parse_args()

    companies = load_config()
    snapshots_by_slug = {c["slug"]: load_company_snapshots(c["slug"]) for c in companies}

    # Read the run summary
    latest_run_path = SNAPSHOTS / "_latest.json"
    run_info = {}
    if latest_run_path.exists():
        try:
            run_info = json.loads(latest_run_path.read_text())
        except Exception:
            run_info = {}

    llm_mode = "no-llm" if args.no_llm else "preview"
    print(f"  LLM mode: {llm_mode}")
    html = render_page(companies, snapshots_by_slug, run_info, llm_mode=llm_mode,
                       only_slug=args.only_slug)

    if args.dry_run:
        print(html[:500])
        print(f"... [total {len(html)} chars]")
    else:
        SITE_CORPUS.mkdir(parents=True, exist_ok=True)
        (SITE_CORPUS / "index.html").write_text(html)
        # Copy the latest_run summary too for reference
        if latest_run_path.exists():
            shutil.copy(latest_run_path, SITE_CORPUS / "_latest_run.json")
        ok = sum(1 for v in snapshots_by_slug.values() if v)
        print(f"  wrote {SITE_CORPUS / 'index.html'} ({len(html)} bytes)")
        print(f"  {ok}/{len(companies)} companies have snapshots")
        print(f"  total snapshots: {sum(len(v) for v in snapshots_by_slug.values())}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
