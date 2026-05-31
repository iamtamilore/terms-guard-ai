# Terms Guard AI

**Live demo → https://newtazer-terms-guard-ai.hf.space**

Paste any Terms & Conditions or Privacy Policy and the AI reads every clause and
flags the parts that are risky or unfair to you, grounded in EU GDPR rules.

## How it works

1. **Split into clauses** — the text is broken into individual sentences and de-duplicated.
2. **Semantic matching** — each clause is embedded with a MiniLM sentence-transformer and
   compared (cosine similarity) against labelled examples of fair vs unfair clauses.
3. **GDPR grounding (RAG)** — a FAISS vector index over GDPR text retrieves the most relevant
   article for each flagged clause, so every concern is tied to a real legal basis.
4. **LLM verification (optional)** — when an `ANTHROPIC_API_KEY` is configured, Claude Haiku
   double-checks each flagged clause to remove false positives. Without it, the tool runs in
   matching-only mode.

## Stack

Python · Flask · sentence-transformers (MiniLM) · FAISS · Anthropic API

## Run locally

```bash
pip install -r requirements.txt
python server.py        # serves the paste-and-analyze page on http://127.0.0.1:7860
```

Optional, for the verification layer:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

Built by **Taiwo Alabi** for an MSc in Artificial Intelligence, National College of Ireland.
Educational project. Not legal advice.
