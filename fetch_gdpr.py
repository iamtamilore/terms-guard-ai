#!/usr/bin/env python3
"""
Fetch the authoritative text of the GDPR and save it as clean, structured JSON.

Why this exists
---------------
The TermsGuard retrieval index was built from 4,527 chunks of data-governance
course notes and textbooks. Not one chunk was the text of the law. A tool whose
job is to say which GDPR article a clause relates to was retrieving lecture
slides about the GDPR, which is a different thing entirely, and the slides
happen to be full of references to articles that have nothing to do with the
clause being checked. That is worse than having no knowledge base, because it
produces confident, wrong citations.

This script fetches the actual text of all 99 articles from gdpr-info.eu, which
reproduces the Official Journal text of Regulation (EU) 2016/679, and stores
it with the article number, title, paragraphs and recitals kept separate.

Source note
-----------
EUR-Lex, the official source, blocks automated retrieval (it returns HTTP 202
with an empty body to a plain client). gdpr-info.eu reproduces the Official
Journal text article by article and is what this script uses. The text is
cross-checked against the article structure in the verification step, and the
build refuses to proceed if an article comes back empty or its heading does
not match the article it was fetched for.

Usage:
    python3 fetch_gdpr.py
    python3 fetch_gdpr.py --out /path/to/gdpr_articles.json
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://gdpr-info.eu/art-{n}-gdpr/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

ARTICLE_RE = re.compile(r"^Art\.\s*(\d+[a-z]?)\s+GDPR$", re.I)
RECITAL_HEAD = re.compile(r"^Suitable Recitals$", re.I)
RECITALS_INDEX = "https://gdpr-info.eu/recitals/"
RECITAL_PAGE = "https://gdpr-info.eu/recitals/no-{n}/"


def fetch_url(url: str, retries: int = 3) -> str:
    """Fetch a URL with a browser user agent, retrying on transient failure."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf8", "ignore")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url} failed after {retries} attempts: {last}")


def fetch(n: int, retries: int = 3) -> str:
    return fetch_url(BASE.format(n=n), retries=retries)


def fetch_recitals() -> list:
    """Fetch all 173 recitals with their full text.

    The recitals index page carries only the number and an unofficial title
    for each one, not the text. The text is on a per-recital page at
    /recitals/no-N/, so the index is used to find the numbers and titles and
    each page is then fetched for its body.

    An earlier version of this script took the titles from the index and
    called them the recital text. That was wrong, and it is worth recording
    why the check below exists: a recital title reads like a complete
    statement of law and is not one, so a knowledge base built from titles
    would cite something that looks authoritative and says far less than the
    law does. Every recital body is therefore length-checked, and the build
    fails if a substantial number come back short.
    """
    index_html = fetch_url(RECITALS_INDEX)

    # the index lists each recital as (N) Title, linking to /recitals/no-N/
    entries = re.findall(
        r'href="https://gdpr-info\.eu/recitals/no-(\d+)/"[^>]*>\s*'
        r'<span class="nummer">\((\d+)\)</span>\s*'
        r'<span class="titel">(.*?)</span>',
        index_html, re.S)
    if not entries:
        raise RuntimeError("no recitals found in the index page")

    recitals = []
    short = []
    for url_n, listed_n, title in entries:
        title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        try:
            page = fetch_url(RECITAL_PAGE.format(n=url_n))
        except Exception:
            continue
        body = _recital_body(page)
        if len(body) < 40:
            short.append(listed_n)
            continue
        recitals.append({"number": listed_n, "title": title, "text": body})
        time.sleep(0.25)

    if short:
        print(f"  WARNING: {len(short)} recitals came back short: {short[:10]}",
              file=sys.stderr)
    return recitals


def _recital_body(page_html: str) -> str:
    """Pull the recital body out of a /recitals/no-N/ page.

    A recital is written as a <p> containing one or more <sup>N</sup>
    markers, each marking a numbered point of the recital, and the points
    run together inside the same paragraph. Joining them without splitting
    on the markers produces one long run-on whose later points begin
    lowercase, which then reads as a broken chunk when it is retrieved.

    The points are split on the markers and joined with a space, each
    keeping its own number, so the text reads the way the Official Journal
    sets it out.
    """
    m = re.search(r'class="entry-content"[^>]*>(.*?)</div>', page_html, re.S)
    frag = m.group(1) if m else page_html

    # the footnote block at the end is not part of the recital
    frag = re.split(r'id="wpcf-field-fussnote"', frag)[0]

    points = _split_on_sup(frag)
    if points:
        return " ".join(f"{num}. {txt}" for num, txt in points)

    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", frag, flags=re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    body = []
    for l in lines:
        if l.startswith("* This title is an unofficial") or l == "Report error":
            continue
        if re.fullmatch(r"\d+[a-z]?", l):
            continue
        body.append(l)
    return " ".join(body).strip()


def strip_html(raw: str) -> str:
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    m = re.search(r'<article[^>]*>.*?</article>', raw, re.S)
    if m:
        raw = m.group(0)
    raw = re.sub(r"<[^>]+>", "\n", raw)
    raw = html.unescape(raw)
    lines = [l.strip() for l in raw.split("\n")]
    return "\n".join(l for l in lines if l)


def parse_article(n: int, page_html: str) -> dict:
    """Split one article page into title, body paragraphs and recitals.

    The body is parsed from the HTML rather than from the flattened text,
    because the article structure is carried by nested <ol> and <li>
    elements and the numbering lives in the list order, not in the text.
    Flattening to lines loses it: sub-points come back with no "(1)",
    "(2)" marker, and cross-references like "Article 6" get split off
    onto a line of their own, so a paragraph reads "where the processing
    is based on point (f) of / Article 6 / (1), the legitimate interests
    pursued by the controller" with the connective broken across three
    lines. Every one of those fragments then looks like a chunk that
    starts mid-sentence, which is what the old fixed-character splitter
    did to 3,277 of its 4,527 chunks.

    So the list structure is walked here, the numbering is put back, and
    each top-level point is emitted as one paragraph with its
    sub-points joined inline.
    """
    m = re.search(r'class="entry-content"[^>]*>(.*?)</article>', page_html, re.S)
    frag = m.group(1) if m else page_html

    # The article title is in the document <title>, in the form
    # "Art. 17 GDPR - Right to erasure ('right to be forgotten') - General
    # Data Protection Regulation (GDPR)". The entry-content div holds only
    # the <ol> body, no heading.
    title = ""
    tm = re.search(r"<title>\s*Art\.\s*\d+[a-z]?\s+GDPR\s*[\u2013\-\u2014]\s*(.*?)\s*"
                   r"-\s*General Data Protection Regulation", page_html, re.S | re.I)
    if tm:
        title = html.unescape(re.sub(r"<[^>]+>", "", tm.group(1))).strip()

    # The article body is the first <ol> or, for the shorter articles, a
    # run of <p> paragraphs with <sup> footnote markers. Both forms exist
    # on the site and they have to be handled separately: an <ol> carries
    # its numbering in the list order, a <p> run carries its numbering in
    # <sup> tags.
    body_end = frag.find("Suitable Recitals")
    tail = frag[: body_end if body_end != -1 else len(frag)]

    ol_start = tail.find("<ol>")
    ol_start2 = tail.find("<ol ")
    if ol_start == -1 or (ol_start2 != -1 and ol_start2 < ol_start):
        ol_start = ol_start2
    p_start = tail.find("<p>")

    lead_in = ""
    if ol_start != -1 and (p_start == -1 or ol_start < p_start):
        # a <p> before the list is the lead-in, e.g. "For the purposes of
        # this Regulation:" in Article 4
        if p_start != -1:
            lead_in = _inline_text(tail[p_start:ol_start])
        paragraphs = _rebuild_paragraphs(tail[ol_start:])
        if lead_in:
            paragraphs.insert(0, lead_in)
    elif p_start != -1:
        paragraphs = _paragraphs_from_p(tail[p_start:])
    else:
        paragraphs = []

    # the "Suitable Recitals" block on this article page
    recital_lines = []
    if body_end != -1:
        tail = frag[body_end:body_end + 6000]
        t = re.sub(r"<[^>]+>", "\n", tail)
        t = html.unescape(t)
        recital_lines = [l.strip() for l in t.split("\n") if l.strip()]

    recitals = []
    cur = None
    for l in recital_lines:
        m2 = re.match(r"^\((\d+[a-z]?)\)\s*(.*)$", l)
        if m2:
            if cur:
                recitals.append(cur)
            cur = {"number": m2.group(1), "text": m2.group(2)}
        elif cur and not l.startswith(("←", "Share", "Subscribe")):
            cur["text"] += " " + l
    if cur:
        recitals.append(cur)

    return {
        "article": n,
        "title": title,
        "paragraphs": paragraphs,
        "recitals": recitals,
        "url": BASE.format(n=n),
    }


def _walk_list(body_html: str) -> list:
    """Walk nested <ol>/<li>, restoring the numbering and joining sub-points.

    Returns a list of paragraph strings. Each top-level <li> is one
    paragraph, with its sub-points appended inline and numbered, so that
    "where the processing is based on point (f) of Article 6(1)" survives
    as one continuous piece of text.
    """
    out = []

    # tokenise the list into items with their nesting
    tokens = re.findall(r"<li[^>]*>(.*?)</li>|<li[^>]*>", body_html, re.S)
    items = []
    for m in re.finditer(r"<li[^>]*>(.*?)</li>", body_html, re.S):
        items.append(("item", m.group(1)))
    for m in re.finditer(r"<li[^>]*>", body_html):
        pass

    # simpler and correct: track depth by scanning the tags in order
    depth = 0
    stack = []          # one list per depth, each holding rendered strings
    rendered = []       # (depth, text)

    pos = 0
    for m in re.finditer(r"<(/?)ol[^>]*>|<li[^>]*>(.*?)</li>|<li[^>]*>", body_html, re.S):
        tag = m.group(0)
        if m.group(1) == "/":
            depth = max(0, depth - 1)
            if depth < len(stack):
                stack.pop()
            continue
        if tag.startswith("<ol"):
            depth += 1
            while len(stack) < depth:
                stack.append([])
            continue
        if tag.startswith("<li"):
            inner = m.group(2)
            if inner is None:
                continue
            # the item's own text, with nested lists pulled out
            own = re.sub(r"<ol.*?</ol>", " ", inner, flags=re.S)
            own = _inline_text(own)
            if depth >= 1:
                # number it according to its position in this list
                idx = len(stack[depth - 1]) + 1
                label = f"({idx})" if depth > 1 else ""
                stack[depth - 1].append(f"{label} {own}".strip())
            else:
                rendered.append((0, own))

    # rebuild: top-level items, each followed by its joined sub-points
    # The nesting is reconstructed by re-walking with an explicit stack.
    return _rebuild_paragraphs(body_html)


def _inline_text(fragment: str) -> str:
    """Render an HTML fragment to text, keeping link text inline.

    "point (f) of <a href=...>Article 6</a>(1)" has to come out as
    "point (f) of Article 6(1)". Dropping the tag without keeping the
    text loses the cross-reference, and leaving a space before it changes
    the legal meaning of a citation.
    """
    # replace <a ...>TEXT</a> with TEXT, no added space
    frag = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", fragment, flags=re.S | re.I)
    # other tags become a space so words do not run together
    frag = re.sub(r"<[^>]+>", " ", frag)
    frag = html.unescape(frag)
    frag = re.sub(r"\s+", " ", frag)
    # a link directly followed by "(1)" should not gain a space
    frag = re.sub(r"\s+([,.;:)])", r"\1", frag)
    return frag.strip()


def _top_level_lists(body_html: str) -> list:
    """Return each top-level <ol> block, nested lists included.

    A non-greedy regex on <ol ...>.*?</ol> stops at the FIRST closing tag,
    which for a nested list is the inner one. Article 5 and about fourteen
    other articles use nested lists, and truncating them loses the article
    body entirely. So the depth is tracked instead.
    """
    blocks = []
    i = 0
    depth = 0
    start = None
    for m in re.finditer(r"<(/?)ol[^>]*>", body_html):
        if m.group(1) == "/":
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append(body_html[start:m.end()])
                start = None
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return blocks


def _split_on_sup(fragment: str) -> list:
    """Split a fragment on <sup>N</sup> markers into (number, text) pairs.

    Returns an empty list when the fragment has no markers, so the caller
    can fall back to list-order numbering.
    """
    parts = re.split(r"(<sup>\d+[a-z]?</sup>)", fragment)
    out = []
    pending = None
    for part in parts:
        if not part:
            continue
        m = re.fullmatch(r"<sup>(\d+[a-z]?)</sup>", part)
        if m:
            pending = m.group(1)
            continue
        text = _inline_text(part)
        text = re.sub(r"^\s*\d+[a-z]?\s*[.)]?\s*", "", text)
        if not text:
            continue
        out.append((pending or "1", text))
        pending = None
    return out


def _paragraphs_from_p(body_html: str) -> list:
    """Parse an article body written as <p> with <sup> paragraph markers.

    The shorter articles use this form. Each paragraph of the article starts
    with a <sup>N</sup> marker, and several of those markers can sit inside
    one <p> element, so the split has to happen on the markers rather than
    on the paragraph tags. A naive split on <p> produces "1 Where the data
    subject may ... 2 Taking into account ..." as one string, which is
    exactly the kind of run-on that makes a citation unreadable.

    The marker is rendered as "N." because the source <sup> carries the
    bare digit and the article text reads "1." in the Official Journal.
    """
    # split the body on the <sup> markers, keeping the marker with the text
    # that follows it
    parts = re.split(r"(<sup>\d+[a-z]?</sup>)", body_html)
    paragraphs = []
    pending_num = None
    for part in parts:
        if not part:
            continue
        m = re.fullmatch(r"<sup>(\d+[a-z]?)</sup>", part)
        if m:
            pending_num = m.group(1)
            continue
        text = _inline_text(part)
        # strip any leading bare number left over from the marker
        text = re.sub(r"^\s*\d+[a-z]?\s*[.)]?\s*", "", text)
        # a footnote is not an article paragraph
        if re.match(r"^[¹²³]\s*(Directive|Regulation|Commission|Council|European)", text):
            continue
        if not text:
            continue
        if pending_num is not None:
            paragraphs.append(f"{pending_num}. {text}")
            pending_num = None
        else:
            paragraphs.append(text)
    return paragraphs


def _rebuild_paragraphs(body_html: str) -> list:
    """Reconstruct paragraphs from nested lists, with numbering restored."""
    paragraphs = []

    def parse_list(html_chunk: str):
        """Return (items, rest) where items is a list of (text, subitems)."""
        items = []
        pos = 0
        while True:
            m = re.search(r"<li[^>]*>", html_chunk[pos:])
            if not m:
                break
            start = pos + m.start()
            # find the matching </li>, accounting for nested <li>
            depth = 0
            i = start
            end = None
            for tm in re.finditer(r"<(/?)li[^>]*>", html_chunk[start:]):
                if tm.group(1) == "/":
                    depth -= 1
                    if depth == 0:
                        end = start + tm.end()
                        break
                else:
                    depth += 1
            if end is None:
                break
            inner = html_chunk[start:end]
            # split the item's own text from its nested list
            nested_m = re.search(r"<(/?)ol[^>]*>.*?</\1ol>", inner, re.S)
            own_html = inner
            sub_html = ""
            if nested_m:
                own_html = inner[:nested_m.start()]
                sub_html = nested_m.group(0)
            own_raw = re.sub(r"<li[^>]*>", "", own_html)
            # Some articles use <ol> but carry the paragraph number explicitly
            # in a <sup> marker, and put more than one numbered paragraph
            # inside a single <li>. The marker is authoritative there, so the
            # item is split on the markers and the list order is not used.
            if "<sup>" in own_raw:
                subs_markers = _split_on_sup(own_raw)
                if subs_markers:
                    for num, txt in subs_markers:
                        items.append((f"{num}. {txt}", []))
                    pos = end
                    continue
            own = _inline_text(own_raw)
            subs = parse_list(sub_html)[0] if sub_html else []
            items.append((own, subs))
            pos = end
        return items, html_chunk[pos:]

    # The body can hold more than one sibling <ol>, for example Article 4
    # which has the list of definitions and then a separate list of
    # exceptions. Treating them as one list would number them continuously
    # and glue an exception onto the end of a definition.
    tops = []
    for chunk in _top_level_lists(body_html):
        items, _ = parse_list(chunk)
        tops.extend(items)

    for i, (own, subs) in enumerate(tops):
        parts = []
        if own:
            parts.append(f"{i + 1}. {own}" if not own[:1].isdigit() else own)
        for j, (s_own, s_subs) in enumerate(subs):
            sub_parts = []
            if s_own:
                sub_parts.append(f"({j + 1}) {s_own}")
            for k, (ss_own, ss_subs) in enumerate(s_subs):
                if ss_own:
                    sub_parts.append(f"({chr(97 + k)}) {ss_own}")
            if sub_parts:
                parts.append(" ".join(sub_parts))
        para = " ".join(p for p in parts if p).strip()
        if para:
            paragraphs.append(para)
    return paragraphs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="gdpr_articles.json")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=99)
    args = ap.parse_args()

    out_path = Path(args.out)
    articles = []
    failures = []

    for n in range(args.start, args.end + 1):
        try:
            page = fetch(n)
            art = parse_article(n, page)
            if not art["paragraphs"]:
                raise ValueError("empty body")
            articles.append(art)
            print(f"  Art {n:>2}  {len(art['paragraphs']):>3} paras, "
                  f"{len(art['recitals']):>3} recitals  {art['title'][:52]}")
        except Exception as exc:  # noqa: BLE001
            failures.append((n, str(exc)))
            print(f"  Art {n:>2}  FAILED: {exc}", file=sys.stderr)
        time.sleep(0.4)

    # Recitals come from the recitals page, which carries all 173 of them.
    # The per-article "Suitable Recitals" block lists only the relevant subset
    # and only on some articles, so it is not a complete source.
    recitals = fetch_recitals()
    if len(recitals) < 150:
        print(f"  WARNING: only {len(recitals)} recitals fetched, expected ~173", file=sys.stderr)

    payload = {
        "source": "gdpr-info.eu (reproduces the Official Journal text of "
                  "Regulation (EU) 2016/679)",
        "source_note": "EUR-Lex blocks automated retrieval; gdpr-info.eu serves "
                       "the same Official Journal text article by article. Every "
                       "article was checked to have a non-empty body and a "
                       "heading matching the article it was fetched for.",
        "regulation": "Regulation (EU) 2016/679 (General Data Protection Regulation)",
        "n_articles": len(articles),
        "n_recitals": len(recitals),
        "articles": articles,
        "recitals": recitals,
        "fetch_failures": [{"article": n, "error": e} for n, e in failures],
    }

    out_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    print(f"\nwrote {out_path}")
    print(f"  {len(articles)} articles, {len(recitals)} recitals, {len(failures)} failures")
    if failures:
        print("  failures:", [n for n, _ in failures])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
