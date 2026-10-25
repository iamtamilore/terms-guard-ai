#!/usr/bin/env python3
"""
Live test of the TermsGuard evidence retrieval, end to end.

The app's new retrieve_gdpr_evidence is anchored to the cited article, so
the passage it shows must come from that article. This loads the app's
modules without booting Flask and walks the classifier + retrieval path
the way a real request does.

Usage: python3 test_live_retrieval.py
"""

import sys
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# load the app's modules the same way the Space does
import gdpr_clauses as g

CASES = [
    ("You can request permanent deletion of your account and all associated data.",
     "Art.17 Right to Erasure", "erasure"),
    ("We will never sell your personal information to third parties.",
     "Art.5(1)(b) Purpose Limitation", "purpose"),
    ("You can withdraw your consent at any time without consequence.",
     "Art.7(3) Right to Withdraw Consent", "withdraw"),
    ("Your data will never be shared with third-party advertisers.",
     "Art.5(1)(b) Purpose Limitation", "purpose"),
    ("You can export all your personal data at any time.",
     "Art.20 Right to Data Portability", "portability"),
    ("You are entitled to lodge a complaint with a supervisory authority.",
     "Art.77 Right to Lodge Complaint", "complaint"),
    ("Your data is encrypted and stored securely.",
     "Art.5(1)(f) Integrity & Confidentiality + Art.32", "security"),
    ("You have the right to object to the processing of your personal data.",
     "Art.21 Right to Object", "object"),
    ("Personal data must be kept no longer than necessary for the purposes collected.",
     "Art.5(1)(e) Storage Limitation", "longer than necessary"),
    ("If a data breach occurs we will notify the supervisory authority within 72 hours.",
     "Art.33 Notification of Data Breach", "personal data breach"),
    ("We process special categories of data only with your explicit consent.",
     "Art.9 Special Categories + Consent", "special categories"),
]


def main() -> int:
    # import the heavy things only here, after the cheap validation
    from sentence_transformers import util
    import torch
    import json

    # load app.py as a module
    import importlib.util
    spec = importlib.util.spec_from_file_location("terms_guard_app", HERE / "app.py")
    app = importlib.util.module_from_spec(spec)

    # the app's __main__ block would run on import; replace it with a no-op
    # so importing the module does not try to listen on a port
    import builtins
    orig_exec = builtins.exec
    try:
        spec.loader.exec_module(app)
    except SystemExit:
        pass  # the app's __main__ calls app.run, which only matters at the bottom

    classify = app.classify_clause
    retrieve = app.retrieve_gdpr_evidence

    passed = 0
    failures = []
    for clause, expected_basis, expected_phrase in CASES:
        label, conf, expl, basis, status, emb = classify(clause)
        if not basis:
            failures.append((clause, expected_basis, "no basis"))
            print(f"  FAIL  '{clause[:50]}'  no basis")
            continue
        # set the slot and call retrieve
        retrieve._last_basis = basis
        evidence = retrieve(emb)
        retrieve._last_basis = ""

        # does the evidence come from the cited article?
        article_match = re.match(r"\s*Art\.?\s*(\d+)", basis)
        cited_article = int(article_match.group(1)) if article_match else None
        art_ok = (cited_article is not None
                  and f"[GDPR Art. {cited_article}]" in evidence)
        phrase_ok = expected_phrase.lower() in evidence.lower() if evidence else False
        basis_ok = (basis.startswith(expected_basis.split()[0]) or
                    expected_basis.split("+")[0].strip() in basis)

        if art_ok and phrase_ok and basis_ok:
            passed += 1
            print(f"  PASS  '{clause[:50]}' -> {basis[:50]}  -> Art {cited_article} text")
        else:
            why = []
            if not basis_ok: why.append(f"basis '{basis}' vs expected '{expected_basis}'")
            if not art_ok: why.append(f"evidence from wrong article, expected Art {cited_article}")
            if not phrase_ok: why.append(f"phrase '{expected_phrase}' missing")
            failures.append((clause, expected_basis, "; ".join(why)))
            print(f"  FAIL  '{clause[:50]}' -> {basis[:50]}  ({'; '.join(why)})")

    total = len(CASES)
    print(f"\n{passed}/{total} passed")
    if failures:
        print("\nfailures:")
        for clause, expected, why in failures:
            print(f"  - {clause[:60]}  ({why})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
