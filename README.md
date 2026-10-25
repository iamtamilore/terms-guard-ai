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

**How it works.** Each clause is embedded with a MiniLM sentence-transformer
and compared against 102 hand-labelled reference clauses (52 fair, 50 unfair).
A FAISS index over 4,527 chunks of data-governance and GDPR context supports
each flagged clause with the passage it was drawn from, labelled as context
rather than as regulation. Up to 15 flagged clauses per analysis are sent to
Claude for verification through one of three routes (direct Anthropic SDK, the
kie.ai gateway, OpenRouter), tried in order with retry. The response carries
verification_source and verification_error, so a silent fallback is
impossible. The previous build reported clauses as model-verified when no
model had actually looked at them; that class of bug is fixed and a 12-case
regression suite passes against the live deployment.

Built by Taiwo Alabi for an MSc in Artificial Intelligence, National College
of Ireland.

Educational project. Not legal advice.
