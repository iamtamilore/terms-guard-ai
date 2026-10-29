#!/usr/bin/env python3
"""
TermsGuard corpus build script.

Reads corpus/snapshots/<slug>/<date>.{txt,json,diff} and emits a static
HTML page at site-build/corpus/index.html.

Pure file-I/O. No network calls. Re-run after snapshot.py.

Output structure:
  /home/newtazer/site-build/corpus/index.html
"""
import argparse
import datetime
import difflib
import json
import shutil
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
    """Return all snapshots for a slug, oldest first, each as a dict."""
    out = []
    d = SNAPSHOTS / slug
    if not d.exists():
        return out
    # Sort by date string (ISO format sorts lexicographically)
    paths = sorted(d.glob("*.json"))
    for p in paths:
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        s = data.get("summary", {})
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


def render_company_card(company: dict, snapshots: list) -> str:
    """One card per company: name, score, sparkline, latest diff."""
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
    Last fetched <span class="mono">{latest['date']}</span>
    {' · changed ' + str(latest['changed_lines']) + ' lines vs prior' if latest['prior'] else ' · first snapshot'}
  </p>
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
  <td class="corpus-date">{prior['date'] if prior else '—'}</td>
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
.diff-line.add .diff-gutter { color: #1a7a32; }
.diff-line.remove .diff-gutter { color: #a02a2a; }
.diff-gutter .marker { font-weight: 700; width: 1ch; text-align: center; }
.diff-content { flex: 1; padding: 0 0.5rem; white-space: pre-wrap;
  word-break: break-word; }
.empty-state { color: var(--text-3); font-style: italic; font-size: var(--text-sm); }

@media (max-width: 700px) {
  .company .metrics { grid-template-columns: repeat(2, auto); }
  .sparkline-cell { max-width: none; grid-column: 1 / -1; }
}
"""


def render_page(companies: list, snapshots_by_slug: dict, run_info: dict) -> str:
    today = datetime.date.today().isoformat()
    table_rows = render_score_table(companies, snapshots_by_slug)
    cards = "\n".join(render_company_card(c, snapshots_by_slug.get(c["slug"], []))
                       for c in companies)
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
<meta property="og:title" content="TermsGuard corpus — EU + Big Tech privacy tracker" />
<meta property="og:description" content="A weekly snapshot of TermsGuard scores for 9 EU and Big Tech companies' privacy policies. Built by Taiwo Alabi." />
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
      category, not by score.
    </div>
  </header>

  <section>
    <h2>Latest scores</h2>
    <p style="color: var(--text-3); font-size: var(--text-sm); max-width: var(--content-max);">
      All 9 companies with a successful fetch. Score is the mean cosine
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
      guarantee of freshness — companies change their policies without
      notice. The numbers reflect the policy as it was on the date shown.
    </p>
    <p style="max-width: var(--content-max); color: var(--text-2); font-size: var(--text-base); line-height: 1.6;">
      The corpus is fetched on a weekly cadence via GitHub Actions.
      Source: <a href="https://github.com/iamtamilore/terms-guard-ai" target="_blank" rel="noopener">terms-guard-ai</a>
      on GitHub. The live demo this corpus powers:
      <a href="https://newtazer-terms-guard-ai.hf.space" target="_blank" rel="noopener">newtazer-terms-guard-ai.hf.space</a>.
    </p>
  </section>

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

    html = render_page(companies, snapshots_by_slug, run_info)

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
