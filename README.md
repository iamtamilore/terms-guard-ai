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

Paste any Terms & Conditions or Privacy Policy and the AI flags the clauses that
are risky or unfair to you, grounded in EU GDPR rules.

**How it works:** each clause is embedded with a MiniLM sentence-transformer and
compared against labelled fair/unfair examples (cosine similarity). A FAISS index
over GDPR text retrieves the relevant article for each flagged clause. When an
`ANTHROPIC_API_KEY` is configured as a Space secret, Claude Haiku verifies each
flagged clause to remove false positives; without it, the tool runs in
matching-only mode.

Built by Taiwo Alabi for an MSc in Artificial Intelligence, National College of Ireland.

Educational project. Not legal advice.
