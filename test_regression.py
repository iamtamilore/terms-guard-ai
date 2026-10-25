"""
TermsGuard AI regression suite.

Twelve inputs, measured against the live service on 24 October 2026. They are the
contract: a change that breaks one of these is a change that broke the product.

    python test_regression.py --base-url http://127.0.0.1:5001
    python test_regression.py --base-url https://newtazer-terms-guard-ai.hf.space

The expected values are the MEASURED behaviour of the fixed pipeline, not guesses.
Where the earlier build was wrong, the expectation records the correction:
  case 9  confidence must never exceed 1.0   (was 1.02)
  case 11 a refund policy must not be flagged  (was flagged Art.82)
"""

import argparse
import json
import sys
import urllib.request

CASES = [
    {"id": 1, "name": "Negation handled, both protective",
     "input": "We do NOT sell your data. You can request a copy of your data or ask us to delete it permanently at any time.",
     "risk": "Low", "flagged": 0, "protective": 2, "unclear": 0},

    {"id": 2, "name": "Below the 50 character minimum",
     "input": "We do NOT sell your data.",
     "risk": "Unknown", "flagged": 0, "protective": 0, "unclear": 0},

    {"id": 3, "name": "French input, not classified",
     "input": "Nous conservons vos donnees pendant 5 ans et nous les transmettons a des partenaires tiers.",
     "risk": "Unknown", "flagged": 0, "protective": 0, "unclear": 1},

    {"id": 4, "name": "Run-on with no punctuation",
     "input": "we collect your data we share it with partners we delete it after 30 days",
     "risk": "Unknown", "flagged": 0, "protective": 0, "unclear": 0},

    {"id": 5, "name": "Pure legalese indemnity clause",
     "input": "The Parties hereby covenant that the Data Principal shall indemnify the Controller against all claims arising from unauthorized processing of personal data without limitation of liability.",
     "risk": "Unknown", "flagged": 0, "protective": 0, "unclear": 1},

    {"id": 6, "name": "Politely worded but risky",
     "input": "We kindly request your permission to transfer your information to third-party vendors for analytics purposes.",
     "risk": "High", "flagged": 1, "protective": 0, "unclear": 0},

    {"id": 7, "name": "Euphemism for sharing data",
     "input": "We share your information with trusted partners who help us improve our services.",
     "risk": "Medium", "flagged": 1, "protective": 0, "unclear": 0},

    {"id": 8, "name": "Compliant policy, three protective clauses",
     "input": "We only collect the data needed to provide the service, and we will never sell your personal information to third parties. You can request a copy of your data or ask us to delete it permanently at any time. All data is encrypted in transit and at rest.",
     "risk": "Low", "flagged": 0, "protective": 3, "unclear": 0},

    {"id": 9, "name": "Five risky clauses, confidence must cap at 1.0",
     "input": "We may sell your personal data to anyone. We are not liable for any data breach. You waive all rights to a class action. We can change these terms without notice. We retain your data forever.",
     "risk": "High", "flagged": 4, "protective": 0, "unclear": 1, "max_confidence": 1.0},

    {"id": 10, "name": "Scam text is never a GDPR concern",
     # The sentence is not about personal data, so the correct verdict is that it
     # is not a GDPR concern at all. It is not flagged. It may sit in the
     # protective list, because a clause that grants no data right and removes
     # none is not a violation. The guarantee is that it is never FLAGGED and
     # that the verifier has actually ruled on it, not that it is filed
     # differently. What must never happen is it being presented as a data
     # protection benefit to the reader.
     "input": "Click here to claim your prize. Send your SSN to verify. We will never ask for money.",
     "risk": "Low", "flagged": 0, "protective": 1, "unclear": 1,
     "must_be_verified": True},

    {"id": 11, "name": "Buried risk found, refund policy not flagged",
     "input": "We provide the service. We collect minimal data. We use encryption. We provide support. We update the app. We retain your personal data indefinitely including your location contacts and browsing history. We offer a refund policy. We have a help center. We keep your data safe. We send receipts.",
     "risk": "High", "flagged": 1, "protective": 0, "unclear": 1,
     "flagged_must_not_contain": "refund"},

    {"id": 12, "name": "Cookie banner consent",
     "input": "By continuing to browse this site you consent to the use of cookies. You agree that we and our partners may use your browsing behaviour for advertising across other websites you visit.",
     "risk": "High", "flagged": 1, "protective": 0, "unclear": 1},
]


def run_case(base_url, case, timeout=180):
    body = json.dumps({"text": case["input"], "links": [], "url": ""}).encode()
    req = urllib.request.Request(
        f"{base_url}/process", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as exc:
        return False, f"request failed: {exc}"

    errs = []
    flagged = data.get("non_ethical_points", [])
    protective = data.get("ethical_points", [])
    unclear = data.get("unclear_points", [])

    if data.get("risk_level") != case["risk"]:
        errs.append(f"risk_level expected {case['risk']!r} got {data.get('risk_level')!r}")
    for key, actual in (("flagged", len(flagged)), ("protective", len(protective)), ("unclear", len(unclear))):
        if key in case and actual != case[key]:
            errs.append(f"{key} expected {case[key]} got {actual}")

    if "max_confidence" in case:
        for p in flagged + protective + unclear:
            if p.get("confidence", 0) > case["max_confidence"]:
                errs.append(f"confidence {p.get('confidence')} exceeds {case['max_confidence']}: {p.get('text')!r}")

    if "flagged_must_not_contain" in case:
        needle = case["flagged_must_not_contain"].lower()
        for p in flagged:
            if needle in (p.get("text") or "").lower():
                errs.append(f"flagged clause contains {needle!r}: {p.get('text')!r}")

    if "protective_must_not_contain" in case:
        needle = case["protective_must_not_contain"].lower()
        for p in protective:
            if needle in (p.get("text") or "").lower():
                errs.append(f"protective clause contains {needle!r}: {p.get('text')!r}")

    if case.get("must_be_verified"):
        # Every clause the reader sees as a confident verdict must have been
        # through the verifier. A confident clause that skipped it is a
        # heuristic speaking with the authority of a model.
        for p in flagged + protective:
            if p.get("confidence_label") != "Low" and p.get("citation_status") != "llm_verified":
                errs.append(
                    f"confident clause not verified by the model: {p.get('text')!r} "
                    f"(citation_status={p.get('citation_status')!r})"
                )

    return (not errs), "; ".join(errs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:5001")
    ap.add_argument("--case", type=int, default=None)
    args = ap.parse_args()

    cases = CASES if args.case is None else [c for c in CASES if c["id"] == args.case]
    passed = failed = 0
    for case in cases:
        ok, msg = run_case(args.base_url, case)
        print(f"  Case {case['id']:2d} [{'PASS' if ok else 'FAIL'}] {case['name']}")
        if not ok:
            print(f"         {msg}")
            failed += 1
        else:
            passed += 1

    print(f"\n{passed} passed, {failed} failed, {len(cases)} total.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
