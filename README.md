---
title: Terms Guard AI
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# Terms Guard AI

Paste any Terms & Conditions or Privacy Policy and the AI flags the clauses
that are risky or unfair to you, grounded in EU GDPR rules.

**How it works.** Each clause is embedded with a MiniLM sentence-transformer
and compared against 102 hand-labelled reference clauses (52 fair, 50 unfair)
that are curated into the file `gdpr_clauses.py`. The article on a flagged
clause is the one the hand-labelled corpus assigned to the closest reference
clause.

Each citation is then anchored to the actual law, not to a search over
loosely related material. The retrieval layer pulls a passage from the
specific article cited, drawn from a knowledge base built from the full
text of Regulation (EU) 2016/679: 99 articles and 173 recitals, fetched
from gdpr-info.eu by `fetch_gdpr.py` and embedded by `build_knowledge_base.py`.
The old index held 4,527 chunks of data-governance course notes, and the
retrieved passage could come from a different article than the one cited.
That failure mode is no longer possible: the passage the reader sees
always carries the same article number as the citation.

Up to 15 flagged clauses per analysis are sent to Claude for verification
through one of three routes (direct Anthropic SDK, the kie.ai gateway,
OpenRouter), tried in order with retry. The response carries
verification_source and verification_error, so a silent fallback is
impossible. The previous build reported clauses as model-verified when no
model had actually looked at them; that class of bug is fixed and a 12-case
regression suite passes against the live deployment.

**Files**
- `fetch_gdpr.py` — pulls the GDPR text and recitals
- `build_knowledge_base.py` — chunks and embeds the law
- `gdpr_clauses.py` — the curated clause → article map (the citation source)
- `gdpr_glosses.py` — plain-English glosses, used only for matching
- `test_kb_retrieval.py`, `test_live_retrieval.py` — verification tests
- `test_regression.py` — the 12-case regression suite
- `gdpr_kb/chunks.json`, `gdpr_kb/index.faiss` — the law text and its FAISS index
- `gdpr_kb/gloss.json`, `gdpr_kb/gloss.faiss` — the glosses and their FAISS index
- `gdpr_articles.json` — the fetched GDPR text, the source for the build

Built by Taiwo Alabi for an MSc in Artificial Intelligence, National College
of Ireland.

Educational project. Not legal advice.
