#!/usr/bin/env python3
"""
Test the rebuilt retrieval system for the GDPR knowledge base.

The index is now two halves:

  - a gloss index, used only to decide WHICH article bears on a clause
  - a law index, used to pull the actual legal text shown to the reader

This test exercises both. Each case gives a clause, the article that a
lawyer would name, and a distinctive phrase from that article's text. The
gloss index should return that article, and the law chunk retrieved for
that article should carry the expected phrase.

The point is not a generic semantic test; it is that the wrong-citation
failure mode the rebuild exists to remove cannot recur. If the gloss
index returns Article 17 for "we will never sell your personal data", the
fix is not done.

Usage:
    python3 test_kb_retrieval.py
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB = HERE / "gdpr_kb"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

CASES = [
    ("You can request permanent deletion of your account and all associated data.",
     17, "erasure"),
    ("We will never sell your personal information to third parties.",
     5, "lawful"),
    ("You can withdraw your consent at any time without consequence.",
     7, "withdraw"),
    ("Your data will never be shared with third-party advertisers.",
     5, "incompatible"),
    ("You can export all your personal data at any time.",
     20, "portability"),
    ("We provide transparent information about data usage.",
     13, "data subject"),
    ("You are entitled to lodge a complaint with a supervisory authority.",
     77, "complaint"),
    ("Your data is encrypted and stored securely.",
     32, "security"),
    ("We will notify you if we intend to process your data for a new purpose.",
     13, "purpose"),
    ("You have the right to object to the processing of your personal data.",
     21, "object"),
    ("Personal data must be kept no longer than necessary for the purposes collected.",
     5, "necessary"),
    ("If a data breach occurs we will notify the supervisory authority within 72 hours.",
     33, "personal data breach"),
    ("We carry out a data protection impact assessment before high risk processing.",
     35, "impact assessment"),
    ("Access to your personal data will be granted to authorised personnel only.",
     32, "personnel"),
    ("We will only collect personal data that is necessary for the stated purpose.",
     5, "purpose"),
    ("We retain personal data for as long as necessary to fulfil the purpose.",
     5, "longer than necessary"),
    ("You can ask us to correct inaccurate personal data.",
     16, "rectification"),
    ("Automated decisions about you can be reviewed by a human.",
     22, "automated decision"),
    ("We process special categories of data only with your explicit consent.",
     9, "special categories"),
    ("Our processor may not engage another processor without our written authorisation.",
     28, "processor"),
]


def main() -> int:
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    chunks = json.loads((KB / "chunks.json").read_text())
    glosses = json.loads((KB / "gloss.json").read_text())
    law_index = faiss.read_index(str(KB / "index.faiss"))
    gloss_index = faiss.read_index(str(KB / "gloss.faiss"))
    model = SentenceTransformer(MODEL_NAME)

    print(f"law: {law_index.ntotal} vectors, {len(chunks)} chunks")
    print(f"gloss: {gloss_index.ntotal} vectors, {len(glosses)} glosses")

    by_article = {}
    for c in chunks:
        m = re.match(r"^\[GDPR Art\. (\d+)\]", c)
        if m:
            by_article.setdefault(int(m.group(1)), []).append(c)

    def cite_article(clause: str) -> int | None:
        """Use the gloss index to decide which article the clause is about."""
        emb = model.encode([clause[:500]], convert_to_numpy=True).astype("float32")
        faiss.normalize_L2(emb)
        _, idx = gloss_index.search(emb, 3)
        for i in idx[0]:
            if 0 <= i < len(glosses):
                return glosses[i]["article"]
        return None

    def article_text(article: int) -> str:
        """Return the law text for an article, as the reader sees it."""
        for c in by_article.get(article, []):
            return c
        return ""

    passed = 0
    failures = []
    for clause, expected, phrase in CASES:
        cited = cite_article(clause)
        text = article_text(cited) if cited else ""
        art_ok = (cited == expected)
        phrase_ok = phrase.lower() in text.lower() if text else False

        if art_ok and phrase_ok:
            passed += 1
            mark = "PASS"
        else:
            mark = "FAIL"
            why = []
            if not art_ok:
                why.append(f"expected Art {expected}, got Art {cited}")
            if not phrase_ok:
                why.append(f"phrase '{phrase}' missing from cited article text")
            failures.append((clause, expected, cited, "; ".join(why)))
        print(f"  {mark}  expected {expected:>2} -> cited {cited}  '{phrase}' {'found' if phrase_ok else 'MISSING'}")

    total = len(CASES)
    print(f"\n{passed}/{total} passed")
    if failures:
        print("\nfailures:")
        for clause, expected, got, why in failures:
            print(f"  expected {expected:>2} got {got}: {clause[:60]}  ({why})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
