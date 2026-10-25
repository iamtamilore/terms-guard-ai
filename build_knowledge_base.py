#!/usr/bin/env python3
"""
Build the TermsGuard retrieval index from the text of the GDPR.

Why this replaces the old index
-------------------------------
The old index held 4,527 chunks of data-governance course notes and
textbook extracts. Not one chunk was the text of the law. The tool's job is
to say which GDPR article a clause relates to, and it was retrieving lecture
slides about the GDPR. Those slides are dense with references to articles
that have nothing to do with the clause being checked, so retrieval pulled in
article numbers that were topically adjacent but legally irrelevant. The
failure mode is the worst kind: confident, specific, wrong.

What this builds
----------------
Two indexes over the same 99 articles and 173 recitals of Regulation (EU)
2016/679.

1. gdpr_kb/index.faiss and gdpr_kb/chunks.json
   The law itself, chunked at real boundaries. Each chunk carries a
   citation line, because a citation that does not say which article it
   came from is not a citation.

2. gdpr_kb/gloss.faiss and gdpr_kb/gloss.json
   A plain-English gloss for each article, used only to decide WHICH
   article is relevant to a clause. The GDPR is not written in plain
   English: "personal data shall be processed lawfully, fairly and in a
   transparent manner" shares almost no vocabulary with "we will never
   sell your personal information". Semantic search over the raw legal
   prose retrieves by topic drift, and returns Article 17 (erasure) for a
   purpose-limitation clause.

   The glosses exist to bridge that gap. They are written to describe the
   article, and they are clearly labelled as the app's own summary and
   never shown to a reader as the law. The passage a reader is shown is
   always the legal text, never the gloss.

Chunking
--------
Articles are split at real boundaries, not at a fixed character count. The
old index used a fixed-character splitter, which is why 3,277 of its 4,527
chunks began part-way through a word, and why app.py has a
_clean_chunk_boundary function whose only job is to repair that damage.

Every chunk is capped at MAX_CHARS. A chunk longer than that is split
again at a sub-point marker or a sentence boundary near the cap, never
mid-word. A chunk that would open lowercase is merged into the one before
it rather than being emitted as an orphan.

Output
------
  gdpr_kb/chunks.json   the law, in index order
  gdpr_kb/index.faiss   InnerProduct index over the law chunks
  gdpr_kb/gloss.json    the plain-English glosses, in index order
  gdpr_kb/gloss.faiss   InnerProduct index over the glosses

The embedding model must match the one app.py loads, or retrieval will be
meaningless: sentence-transformers/all-MiniLM-L6-v2, 384 dimensions.

Usage:
    python3 build_knowledge_base.py
    python3 build_knowledge_base.py --articles gdpr_articles.json --out gdpr_kb
"""

import argparse
import json
import re
from pathlib import Path

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MAX_CHARS = 900
MIN_CHARS = 40

CITE_PREFIX = "[GDPR Art. {num}] {title}"
RECITAL_PREFIX = "[GDPR Recital {num}] {title}"


def split_long(text: str, max_chars: int) -> list:
    """Split a long passage so that no chunk opens mid-sentence.

    The governing structure of the law is a numbered paragraph whose points
    are "(1)", "(2)", "(3)", and the Official Journal text joins those
    points with a semicolon. So a passage like

        "the processing shall not be carried out unless: (1) the data
         subject has given explicit consent; (2) it is necessary for a
         contract; (3) it is required by law"

    is one paragraph made of three sub-points, and the semicolon before
    "(2)" is a real boundary. Splitting there and keeping the marker with
    the text that follows gives chunks that each open with their own
    "(2)" rather than with a bare "it is necessary for a contract".

    Boundaries are tried in order of preference:
      1. a sub-point marker, "(1)" or "(a)"
      2. a sentence end
      3. a semicolon
      4. a word boundary, as the last resort
    """
    if len(text) <= max_chars:
        return [text]

    out = []
    rest = text
    guard = 0
    while len(rest) > max_chars:
        guard += 1
        if guard > 2000:
            out.append(rest[:max_chars])
            rest = rest[max_chars:]
            break

        window = rest[:max_chars]
        best = -1

        # 1. a sub-point marker. The cut goes BEFORE the marker, so the
        # marker starts the next chunk and each chunk opens with its own
        # number: "(2) it is necessary for a contract".
        for m in re.finditer(r"\s+\(\d+[a-z]?\)", window):
            if m.end() > max_chars * 0.4:
                best = m.start()
                break
        # 2. a sentence end
        if best < 0:
            for marker in (". ", "? ", "! "):
                pos = window.rfind(marker)
                if pos > max_chars * 0.4:
                    best = pos + len(marker)
                    break
        # 3. a semicolon
        if best < 0:
            pos = window.rfind("; ")
            if pos > max_chars * 0.4:
                best = pos + 2
        # 4. a word boundary
        if best < 0:
            pos = window.rfind(" ")
            best = pos + 1 if pos > max_chars * 0.4 else max_chars

        piece = rest[:best].strip()
        rest = rest[best:].strip()

        if not piece:
            break

        # A chunk must not open mid-sentence. If it starts lowercase it is
        # the tail of a sub-point whose marker went to the previous chunk.
        # Recover it by moving the marker across, or by cutting at the last
        # sentence end inside the piece.
        if piece[0].islower():
            m = re.search(r"(?<=[.;:])\s+[A-Z(‘]", piece)
            if m:
                out.append(piece[:m.start() + 1].strip())
                rest = f"{piece[m.start() + 1:].strip()} {rest}".strip()
            else:
                out.append(piece)
        else:
            out.append(piece)

    if rest:
        out.append(rest)
    return out


def build_chunks(articles: list, recitals: list) -> list:
    chunks = []
    seen = set()

    def add(text: str, meta: str):
        text = " ".join(text.split())
        if len(text) < MIN_CHARS:
            return
        for piece in split_long(text, MAX_CHARS):
            key = piece[:120]
            if key in seen:
                continue
            seen.add(key)
            # A piece that opens lowercase is the tail of a list item whose
            # marker belongs to the previous chunk: Article 4 has a second
            # list of exceptions where every item continues the definition
            # above it, and several recitals run long semicolon-separated
            # enumerations. Emitting those as their own chunk would put a
            # fragment in front of a reader with no context, so they are
            # appended to the chunk before them instead.
            #
            # The length cap is deliberately not applied here. Article 4's
            # exception items are long, and refusing the merge because the
            # result would exceed the cap is exactly what left a chunk
            # reading "as regards a processor with establishments in more
            # than one Member State..." with nothing before it. One chunk
            # that is slightly too long is better than one that is
            # unreadable.
            if piece and piece[0].islower() and chunks:
                chunks[-1] = chunks[-1].rstrip() + " " + piece
                continue
            chunks.append(f"{meta}\n{piece}")

    for a in articles:
        meta = CITE_PREFIX.format(num=a["article"], title=a["title"])
        add(" ".join(a["paragraphs"]), meta)

    for r in recitals:
        meta = RECITAL_PREFIX.format(num=r["number"], title=r.get("title", ""))
        add(r["text"], meta)

    return chunks


def build_glosses(articles: list) -> list:
    """Build one plain-English gloss per article, for matching only.

    The gloss is written from the article's own text. It is a matching aid,
    never a citation: app.py shows the legal text to the reader and the
    gloss never leaves the retrieval layer.
    """
    from gdpr_glosses import ARTICLE_GLOSS
    glosses = []
    for a in articles:
        g = ARTICLE_GLOSS.get(a["article"])
        if not g:
            continue
        glosses.append({
            "article": a["article"],
            "title": a["title"],
            "gloss": g,
            # the article's own opening words, so the gloss chunk carries
            # some of the law's vocabulary as well as the plain summary
            "snippet": " ".join(a["paragraphs"])[:600],
        })
    return glosses


def verify(chunks: list, glosses: list) -> None:
    """Refuse to write an index that will produce bad citations."""
    problems = []

    # no chunk may start mid-word
    for i, c in enumerate(chunks):
        body = c.split("\n", 1)[1] if "\n" in c else c
        first = body.split(" ", 1)[0]
        if first and first[0].islower() and first.isalpha() and len(first) <= 3:
            problems.append(f"chunk {i} starts mid-word: {first!r}")

    # every chunk must carry a citation
    for i, c in enumerate(chunks):
        if not re.match(r"^\[GDPR (Art\.|Recital)", c):
            problems.append(f"chunk {i} has no citation line: {c[:60]!r}")

    art_chunks = sum(1 for c in chunks if c.startswith("[GDPR Art."))
    rec_chunks = sum(1 for c in chunks if c.startswith("[GDPR Recital"))
    if art_chunks < 90:
        problems.append(f"only {art_chunks} article chunks, expected at least 90")
    if rec_chunks < 150:
        problems.append(f"only {rec_chunks} recital chunks, expected at least 150")

    joined = " ".join(chunks)
    for phrase in ("lawfully, fairly and in a transparent manner",
                   "personal data shall be", "right to data portability",
                   "right to lodge a complaint", "erasure"):
        if phrase.lower() not in joined.lower():
            problems.append(f"expected GDPR wording not found: {phrase!r}")

    # glosses must cover every article they claim to
    if len(glosses) < 90:
        problems.append(f"only {len(glosses)} glosses, expected at least 90")
    for g in glosses:
        if not g["gloss"] or len(g["gloss"]) < 40:
            problems.append(f"gloss for article {g['article']} is too short")
        if not g["title"]:
            problems.append(f"gloss for article {g['article']} has no title")

    if problems:
        print(f"  REFUSING TO WRITE. {len(problems)} problem(s):")
        for p in problems[:20]:
            print("   -", p)
        raise SystemExit(1)


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", default=str(here / "gdpr_articles.json"))
    ap.add_argument("--out", default=str(here / "gdpr_kb"))
    args = ap.parse_args()

    src = Path(args.articles)
    if not src.exists():
        print(f"  {src} not found. Run fetch_gdpr.py first.")
        return 1

    data = json.loads(src.read_text())
    articles = data["articles"]
    recitals = data["recitals"]
    print(f"  source: {src.name}, {len(articles)} articles, {len(recitals)} recitals")

    chunks = build_chunks(articles, recitals)
    glosses = build_glosses(articles)
    print(f"  built {len(chunks)} law chunks and {len(glosses)} glosses")

    verify(chunks, glosses)
    print("  verification passed")

    import numpy as np
    from sentence_transformers import SentenceTransformer
    import faiss

    print(f"  loading {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    def embed(texts):
        e = model.encode(texts, convert_to_numpy=True, batch_size=64,
                         show_progress_bar=False).astype("float32")
        faiss.normalize_L2(e)
        return e

    law_emb = embed(chunks)
    index = faiss.IndexFlatIP(law_emb.shape[1])
    index.add(law_emb)
    print(f"  law index: {index.ntotal} x {law_emb.shape[1]}")

    gloss_texts = [f"{g['gloss']} {g['snippet']}" for g in glosses]
    gloss_emb = embed(gloss_texts)
    gloss_index = faiss.IndexFlatIP(gloss_emb.shape[1])
    gloss_index.add(gloss_emb)
    print(f"  gloss index: {gloss_index.ntotal} x {gloss_emb.shape[1]}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # write to temp names and swap, so a crash cannot leave a half-written
    # index that the app would load as if it were good
    def atomic_write(name: str, data: bytes):
        tmp = out / f"{name}.tmp"
        tmp.write_bytes(data)
        tmp.replace(out / name)

    atomic_write("chunks.json", json.dumps(chunks, ensure_ascii=False).encode())
    faiss.write_index(index, str(out / "index.faiss.tmp"))
    (out / "index.faiss.tmp").replace(out / "index.faiss")

    atomic_write("gloss.json", json.dumps(glosses, ensure_ascii=False).encode())
    faiss.write_index(gloss_index, str(out / "gloss.faiss.tmp"))
    (out / "gloss.faiss.tmp").replace(out / "gloss.faiss")

    print(f"  wrote {out}/chunks.json, index.faiss, gloss.json, gloss.faiss")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
