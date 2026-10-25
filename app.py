# ============================================
# MACOS MULTIPROCESSING FIX (MUST BE FIRST)
# ============================================
import multiprocessing
import os

try:
    multiprocessing.set_start_method('spawn', force=True)
except RuntimeError:
    pass

os.environ['OBJC_DISABLE_INITIALIZE_FORK_SAFETY'] = 'YES'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ['MKL_NUM_THREADS'] = '4'
os.environ['NUMEXPR_NUM_THREADS'] = '4'
os.environ['OMP_NUM_THREADS'] = '4'

# ============================================
# IMPORTS
# ============================================
import json
import re
import time
import warnings
from pathlib import Path

import torch
from flask import Flask, request, jsonify
from flask_cors import CORS
from sentence_transformers import SentenceTransformer, util
from transformers import AutoTokenizer, AutoModelForCausalLM

from gdpr_clauses import (
    ETHICAL_REFERENCE_CLAUSES,
    NON_ETHICAL_REFERENCE_CLAUSES,
    ETHICAL_TEXTS,
    NON_ETHICAL_TEXTS,
)

try:
    import feedback_log
    FEEDBACK_LOGGING = True
except Exception:
    FEEDBACK_LOGGING = False

warnings.filterwarnings('ignore')

app = Flask(__name__)
CORS(app)

# ============================================
# MODEL LOADING (GLOBAL — LOAD ONCE)
# ============================================
print("=" * 55)
print("  TERMS GUARD AI — loading models ...")
print("=" * 55)
_t0 = time.time()

# --- Embedding model (classification, RAG retrieval) ---
semantic_model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
print(f"  ✅ MiniLM-L6-v2 loaded")

# --- No local generation model ---
# Summaries are now built deterministically from results, and clause verification uses the
# Anthropic API (Haiku). No local LLM is loaded, which frees ~3 GB of disk and GPU memory.
USE_QWEN = False
qwen_model = None
qwen_tokenizer = None
qwen_device = "cpu"
print("  ✅ No local generation model (deterministic summary + Haiku API verification)")

torch.set_num_threads(4)

# --- GDPR Knowledge Base (optional — built by build_knowledge_base.py) ---
_KB_DIR = Path(__file__).parent / "gdpr_kb"
gdpr_kb_index = None
gdpr_kb_chunks: list[str] = []

try:
    import faiss as _faiss_lib

    if (_KB_DIR / "index.faiss").exists() and (_KB_DIR / "chunks.json").exists():
        gdpr_kb_index = _faiss_lib.read_index(str(_KB_DIR / "index.faiss"))
        gdpr_kb_chunks = json.loads((_KB_DIR / "chunks.json").read_text())
        print(f"  ✅ GDPR knowledge base: {gdpr_kb_index.ntotal} vectors, {len(gdpr_kb_chunks)} chunks")
    else:
        print("  ℹ️  No GDPR KB found — run build_knowledge_base.py to enable RAG context")
except ImportError:
    print("  ℹ️  faiss not available — GDPR RAG context disabled")

print(f"  ✅ All models ready in {time.time() - _t0:.1f}s")
print("=" * 55)

# ============================================
# PRE-COMPUTE REFERENCE EMBEDDINGS (once at startup)
# ============================================
ethical_embeddings = semantic_model.encode(ETHICAL_TEXTS, convert_to_tensor=True)
non_ethical_embeddings = semantic_model.encode(NON_ETHICAL_TEXTS, convert_to_tensor=True)

# ============================================
# KEYWORD SCORING (supplementary only)
# ============================================
ETHICAL_KEYWORDS = {
    'delete your data': 2.0, 'never sell': 2.0, 'explicit consent': 2.0,
    'opt-out': 1.5, 'encrypted': 1.5, 'secure': 1.0, 'transparent': 1.5,
    'gdpr complian': 2.0, 'ccpa complian': 2.0,
}

NON_ETHICAL_KEYWORDS = {
    'sell your data': 2.0, 'track your': 1.5, 'waive': 2.0,
    'no liability': 2.0, 'not liable': 2.0, 'indefinitely': 2.0,
    'must accept': 1.0, 'irrevocable': 1.5,
}

# ============================================
# CONFIDENCE TIER CONSTANTS (tunable)
# ============================================
CONF_HIGH = 0.55
CONF_MEDIUM = 0.45
# Below this, the clause is too weak to be worth a verifier call. A Medium
# confidence clause is included, because dropping it on a cosine score alone is
# how a false positive reaches the reader.
CONF_VERIFY_MIN = 0.35

# LLM verification — Qwen post-filters cosine candidates to kill false positives.
# Disable if latency budget is tight (set LLM_VERIFY = False).
LLM_VERIFY = True
LLM_VERIFY_MAX_CANDIDATES = 10  # one batched Haiku call; capped at 10 for speed
                                # (~30s vs ~60s) while still showing plenty of verified flags


def confidence_tier(score: float) -> str:
    if score >= CONF_HIGH:
        return "High"
    if score >= CONF_MEDIUM:
        return "Medium"
    return "Low"


# ============================================
# PROTECTIVE PHRASE DETECTION
# ============================================
PROTECTIVE_PHRASES = [
    "not intended to restrict", "in no way intended to",
    "deemed to waive", "shall not waive", "not waive, preclude",
    "cannot be limited by", "can't be limited",
    "nothing will affect your statutory", "will not affect your statutory",
    "does not restrict your", "do not limit your rights",
    "without prejudice to your rights", "nothing herein will be deemed",
    "nothing herein shall", "statutory rights of a consumer",
    "rights that can't be limited", "rights that cannot be limited",
    "does not limit your", "shall not affect your",
    "will not affect your rights",
]


def detect_protective_context(text: str) -> bool:
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in PROTECTIVE_PHRASES)


def get_keyword_boost(text: str):
    text_lower = text.lower()
    eth_boost, non_eth_boost = 0.0, 0.0
    matched_eth, matched_non = [], []

    for kw, w in ETHICAL_KEYWORDS.items():
        if kw in text_lower:
            eth_boost += w * 0.01
            matched_eth.append(kw)

    # Suppress non-ethical keyword boosts when protective context is present
    if not detect_protective_context(text):
        for kw, w in NON_ETHICAL_KEYWORDS.items():
            if kw in text_lower:
                non_eth_boost += w * 0.01
                matched_non.append(kw)

    parts = []
    if matched_eth:
        parts.append(f"Ethical signals: {', '.join(matched_eth[:2])}")
    if matched_non:
        parts.append(f"Non-ethical signals: {', '.join(matched_non[:2])}")

    return eth_boost, non_eth_boost, "; ".join(parts)


def detect_critical_negations(text: str) -> bool:
    text_lower = text.lower()
    for phrase in ('not liable', 'not responsible', 'does not track', 'do not sell',
                   'never track', 'never sell', 'no tracking', 'no liability'):
        if phrase in text_lower:
            return True
    return False


_CHILD_TERMS = ('child', 'children', 'minor', 'minors', 'kid', 'kids',
                'under 13', 'under 16', 'under 18', 'underage', 'under-age',
                'parental consent', 'guardian consent', 'age verification',
                'verify the age', 'verify age', 'coppa')


def _is_children_clause(text: str) -> bool:
    """True if a clause concerns children's / minors' data (Art.8 territory)."""
    t = text.lower()
    return any(term in t for term in _CHILD_TERMS)


# ============================================
# CLASSIFICATION (semantic-primary, GDPR-aware)
# ============================================
def classify_clause(sentence: str, emb=None):
    """
    Returns (label, confidence, explanation, gdpr_basis, citation_status, emb_tensor).
    emb_tensor is returned for reuse in KB evidence lookup — avoids double-encoding.
    citation_status: 'supported' | 'tentative' | 'weak' based on raw cosine score.
    Pass `emb` (a precomputed embedding) to skip per-clause encoding — the caller
    batch-encodes all clauses in one vectorized call, which is far faster on CPU.
    """
    if emb is None:
        emb = semantic_model.encode(sentence, convert_to_tensor=True)

    eth_scores = util.cos_sim(emb, ethical_embeddings)[0]
    non_eth_scores = util.cos_sim(emb, non_ethical_embeddings)[0]

    best_eth_idx = int(torch.argmax(eth_scores))
    best_non_idx = int(torch.argmax(non_eth_scores))
    raw_eth = eth_scores[best_eth_idx].item()
    raw_non = non_eth_scores[best_non_idx].item()

    max_eth = raw_eth
    max_non = raw_non

    eth_boost, non_boost, kw_explanation = get_keyword_boost(sentence)
    max_eth += eth_boost
    max_non += non_boost

    if detect_critical_negations(sentence):
        max_eth += 0.02

    if detect_protective_context(sentence):
        # A soft prior, not a guarantee. The old hard override forced ethical to win
        # and could not be overturned by the LLM verifier, so a protective-looking
        # phrase could never be re-checked. Now it only nudges the boundary.
        max_eth += 0.10

    top_score = max(max_eth, max_non)
    score_diff = abs(max_eth - max_non)

    if top_score < 0.35 or score_diff < 0.05:
        return 'unclear', round(min(top_score, 1.0), 2), "Low confidence or unclear classification", "", "weak", emb

    explanation = kw_explanation or "Classified via semantic analysis"
    if detect_critical_negations(sentence):
        explanation += " [Negation detected]"
    if detect_protective_context(sentence):
        explanation += " [Protective clause]"

    if max_eth > max_non:
        raw_winner = raw_eth
        gdpr_basis = ETHICAL_REFERENCE_CLAUSES[best_eth_idx]["gdpr"]
        label = 'ethical'
        final_conf = round(min(max_eth, 1.0), 2)
    else:
        raw_winner = raw_non
        gdpr_basis = NON_ETHICAL_REFERENCE_CLAUSES[best_non_idx]["gdpr"]
        label = 'non_ethical'
        final_conf = round(min(max_non, 1.0), 2)

    # Citation grounded in raw semantic score (before keyword/protective boosts)
    if raw_winner >= CONF_HIGH:
        citation_status = "supported"
    elif raw_winner >= CONF_MEDIUM:
        citation_status = "tentative"
    else:
        citation_status = "weak"
        gdpr_basis = ""

    return label, final_conf, explanation, gdpr_basis, citation_status, emb


# ============================================
# GDPR RAG RETRIEVAL
# ============================================
def retrieve_gdpr_context(text_snippet: str, k: int = 3) -> str:
    """Return top-k relevant GDPR/DG chunks for the given text snippet."""
    if gdpr_kb_index is None or not gdpr_kb_chunks:
        return ""
    import faiss as _faiss_lib
    import numpy as np

    emb = semantic_model.encode([text_snippet[:500]], convert_to_numpy=True).astype("float32")
    _faiss_lib.normalize_L2(emb)
    _, indices = gdpr_kb_index.search(emb, k)
    return "\n---\n".join(
        gdpr_kb_chunks[i] for i in indices[0] if 0 <= i < len(gdpr_kb_chunks)
    )


def retrieve_gdpr_evidence(emb_tensor, k: int = 1) -> str:
    """Return top KB chunk for a pre-computed embedding — no re-encoding."""
    if gdpr_kb_index is None or not gdpr_kb_chunks:
        return ""
    import faiss as _faiss_lib
    emb_np = emb_tensor.cpu().numpy().astype("float32").reshape(1, -1)
    _faiss_lib.normalize_L2(emb_np)
    _, indices = gdpr_kb_index.search(emb_np, k)
    chunks = [gdpr_kb_chunks[i] for i in indices[0] if 0 <= i < len(gdpr_kb_chunks)]
    if not chunks:
        return ""
    # Cut on a word boundary. A hard character cut lands mid-word and the
    # reader sees "pen on purpose" instead of "happen on purpose", which makes
    # the citation look garbled rather than quoted.
    text = " ".join(chunks[0].split())
    if len(text) <= 400:
        return text
    cut = text[:400]
    if " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut + "…"


# ============================================
# SUMMARY GENERATION (Qwen2.5-1.5B or flan-t5 fallback)
# ============================================
def generate_summary(text_sample: str, gdpr_context: str = "") -> str:
    if USE_QWEN:
        ctx_part = f"\nRelevant GDPR context:\n{gdpr_context}\n" if gdpr_context else ""
        user_msg = (
            f"{ctx_part}"
            "Summarize the main privacy risks and protections in these Terms & Conditions "
            "in ONE concise sentence, citing specific GDPR articles where applicable:\n\n"
            f"{text_sample}"
        )
        messages = [
            {"role": "system", "content": "You are a privacy analyst specialising in GDPR compliance. Be concise and cite GDPR articles."},
            {"role": "user", "content": user_msg},
        ]
        formatted = qwen_tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = qwen_tokenizer(
            formatted, return_tensors="pt", max_length=1024, truncation=True
        ).to(qwen_device)

        with torch.no_grad():
            outputs = qwen_model.generate(
                **inputs,
                max_new_tokens=90,
                do_sample=False,
                pad_token_id=qwen_tokenizer.eos_token_id,
            )
        new_tokens = outputs[0][inputs.input_ids.shape[1]:]
        return qwen_tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
    else:
        # flan-t5-base fallback
        prompt = (
            "Analyze these terms and identify the main privacy risks and protections "
            f"in one sentence:\n{text_sample}\n\nSummary:"
        )
        inputs = qwen_tokenizer(prompt, return_tensors="pt", max_length=512, truncation=True)
        outputs = qwen_model.generate(inputs.input_ids, max_length=60, num_beams=2, early_stopping=True)
        return qwen_tokenizer.decode(outputs[0], skip_special_tokens=True)


# ============================================
# LLM VERIFICATION (Claude Haiku 4.5 via Anthropic API)
# ============================================
# Replaces local-model verification — the 1.5B model hallucinated articles and
# blew the latency budget. Haiku understands clause meaning, returns guaranteed-valid
# JSON via structured outputs, and runs in ~1s. Requires ANTHROPIC_API_KEY in the env.
# If the SDK or key is missing, LLM_VERIFY_AVAILABLE stays False and the pipeline
# falls back to cosine-only classification (never crashes).
anthropic_client = None
LLM_VERIFY_AVAILABLE = False
_LLM_MODEL = "claude-haiku-4-5"
_LLM_PROMPT_VERSION = "v4-2026-06-03"  # bump when _LLM_SYSTEM_PROMPT changes — logged per scan
                                        # v2: added Art.8 children's-data / COPPA detection

try:
    import anthropic
    from pydantic import BaseModel

    class ClauseVerdict(BaseModel):
        id: int
        concern: bool
        article: str
        reason: str

    class VerificationResult(BaseModel):
        verdicts: list[ClauseVerdict]

    if os.environ.get("ANTHROPIC_API_KEY"):
        anthropic_client = anthropic.Anthropic()
        LLM_VERIFY_AVAILABLE = True
        print(f"  ✅ LLM verification: {_LLM_MODEL} (Anthropic API)")
    else:
        print("  ℹ️  ANTHROPIC_API_KEY not set — LLM verification disabled (cosine-only fallback)")
except ImportError:
    print("  ℹ️  anthropic SDK not installed — LLM verification disabled (cosine-only fallback)")


_LLM_SYSTEM_PROMPT = """You are a GDPR and EU consumer-rights compliance analyst reviewing Terms & Conditions \
clauses for risks to the user. For each numbered clause, judge it against the framework below and \
return a structured verdict.

Each clause carries a label in brackets: either (non_ethical) or (ethical).

For a clause labelled (non_ethical), concern=true if it is a GENUINE GDPR or EU \
consumer-rights concern, concern=false if the cosine stage was wrong and it is actually \
fine. Generic commercial statements that are not about data protection, for example \
"We offer a refund policy" or "You can contact our support team", are NOT GDPR clauses \
in either direction. Mark them concern=false, article="", and say so in the reason.

For a clause labelled (ethical), the cosine stage believes it grants or preserves a data \
subject right, so it is being given the benefit of the doubt. Set concern=false only if it \
really is a genuine protection of a data subject right. If it is a marketing line, a scam \
phrase, a generic commercial statement, or is unrelated to data protection, set \
concern=true and give the article it actually violates, or empty string if no GDPR article \
applies. Do not accept a clause as protective just because it contains reassuring words. \
For example "We will never ask for money" is not a GDPR protection, it is not about \
personal data, so treat it as not a concern on data-protection grounds and set \
concern=false with article="".

A clause IS a concern if it does any of:
- Limits or waives the user's legal rights (class-action waivers, liability caps, forced arbitration)
- Allows data handling that breaches GDPR principles (selling/sharing data, tracking, indefinite \
retention, vague purposes, transfers to weak-privacy countries)
- Relies on weak consent (continued-use = acceptance, pre-ticked, bundled, no withdrawal)
- Permits unilateral changes with no real notice, or denies access/erasure/portability

A clause is NOT a concern if it is: standard account-security advice, navigational/section text, \
pro-user (grants rights, allows free deletion/cancellation), or a protective clause that preserves \
the user's statutory rights.

CHOOSE the single best legal basis from this menu (use the exact label):
- "Art.5(1)(b) Purpose Limitation"   - data used beyond stated purpose / sold / shared
- "Art.5(1)(e) Storage Limitation"   - indefinite / excessive retention
- "Art.6 Lawful Basis"               - processing without a valid legal basis
- "Art.8 Children's Consent"         - collecting/processing children's or minors' data with \
no parental consent or age verification (also flag likely COPPA exposure for US under-13 users)
- "Art.7 Consent"                    - no valid/affirmative consent, implied consent
- "Art.7(3) Withdraw Consent"        - cannot withdraw consent
- "Art.13/14 Right to be Informed"   - unilateral changes, no notice, hidden practices
- "Art.15 Access" / "Art.17 Erasure" / "Art.20 Portability" - blocks these rights
- "Art.32 Security" / "Art.33 Breach Notification" - inadequate security / breach handling
- "Art.44 International Transfers"    - transfers without safeguards
- "Art.82 Liability"                 - caps/excludes liability for damage
- "Consumer Rights (UCTA)"           - unfair contract term / class-action waiver / arbitration \
where NO GDPR article cleanly applies
- "" (empty)                         - when concern is false

Prefer "Consumer Rights (UCTA)" over forcing an ill-fitting GDPR article. Keep reason to max 12 words.

OVERRIDE RULE: if a clause mentions children, minors, kids, "under 13", "under 16", \
age, parental/guardian consent, or age verification, you MUST use article \
"Art.8 Children's Consent" (not Art.6 or Art.7), and add "(COPPA risk)" to the reason. \
This takes priority over any other article choice.

Examples:
[1] "We may sell your data to advertisers." -> concern=true, article="Art.5(1)(b) Purpose Limitation", reason="Sells personal data beyond service purpose"
[2] "You waive the right to a class action." -> concern=true, article="Consumer Rights (UCTA)", reason="Waives collective redress; unfair contract term"
[3] "You may delete your account at any time." -> concern=false, article="", reason="Pro-user deletion right"
[4] "We collect data from children without parental consent." -> concern=true, article="Art.8 Children's Consent", reason="Minors' data, no parental consent (COPPA risk too)"
[4] "You are responsible for keeping your password secure." -> concern=false, article="", reason="Standard account-security advice\""""


def verify_clauses_with_llm(candidates: list) -> dict:
    """
    One Claude Haiku call to verify whether each cosine-flagged non-ethical clause
    is a GENUINE GDPR / consumer-rights concern or a false positive.
    Uses structured outputs (Pydantic schema) so the response is guaranteed-valid JSON.
    Returns {0-based-index: {"concern": bool, "article": str, "reason": str}}.
    On any failure returns {} — caller falls back to cosine results. Never crashes.
    """
    if not LLM_VERIFY_AVAILABLE or not candidates:
        return {}

    try:
        clause_lines = "\n".join(
            f'[{i + 1}] ({c.get("label", "non_ethical")}) "{c["text"][:300]}"'
            for i, c in enumerate(candidates)
        )
        response = anthropic_client.messages.parse(
            model=_LLM_MODEL,
            max_tokens=2048,  # ~100 tokens/verdict headroom for up to 20 candidates
            system=[{
                "type": "text",
                "text": _LLM_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # caches once prompt grows past the min prefix
            }],
            messages=[{"role": "user", "content": clause_lines}],
            output_format=VerificationResult,
        )

        result = response.parsed_output
        if result is None:
            print("  ⚠️  LLM verify: model refused or returned no parseable output")
            return {}

        verdicts = {}
        for item in result.verdicts:
            idx = item.id - 1  # 1-based prompt id → 0-based index
            if 0 <= idx < len(candidates):
                verdicts[idx] = {
                    "concern": item.concern,
                    "article": item.article.strip(),
                    "reason": item.reason.strip()[:120],
                }
        return verdicts

    except Exception as exc:
        print(f"  ⚠️  LLM verify failed ({type(exc).__name__}): {exc}")
        return {}


# ============================================
# CLAUSE QUALITY FILTER
# ============================================
def is_real_clause(text: str) -> bool:
    """Reject headings, table-of-contents lines, numerals and truncated fragments
    so they never reach classification (kills the obvious false positives)."""
    t = (text or "").strip()
    if len(t) < 25:                      # too short to be a real clause
        return False
    words = t.split()
    if len(words) < 5:                   # headings like "How to Contact Us"
        return False
    # must contain a verb-ish lowercase word — headings are mostly Title Case
    lower_words = [w for w in words if w[:1].islower()]
    if len(lower_words) < 2:
        return False
    # reject lines that are mostly a roman numeral / section label tail
    if re.match(r'^(?:[IVXLC]+|[0-9]+(?:\.[0-9]+)*)\b', t) and len(words) < 8:
        return False
    # reject fragments that start mid-sentence (lowercase or punctuation)
    if t[0] in ',;:)' or (t[0].islower() and not t.lower().startswith(('we ', 'you ', 'your '))):
        return False
    # reject fragments that end mid-word (no terminal punctuation AND ends with "(e" etc.)
    if re.search(r'\(\s*[a-z]$', t):
        return False
    # real clauses end in sentence punctuation; headings/labels do not
    # ("How we share your personal information", "Supplement and Update of the Policy")
    if t[-1] not in '.!?;:)"\'' and not t.endswith(('etc', 'etc.')):
        return False
    return True


# ============================================
# MAIN ENDPOINT
# ============================================
@app.route('/process', methods=['POST'])
def process_text():
    t_start = time.time()
    data = request.json
    text = data.get('text', '')
    incoming_links = data.get('links', [])

    if not text or len(text) < 50:
        return jsonify({
            "ethical_points": [], "non_ethical_points": [], "unclear_points": [],
            "risk_level": "Unknown",
            "summary": "Please navigate to a page with more content.",
            "processing_time_seconds": 0,
            "highlighted_indices": [],
            "total_text_analyzed_chars": 0,
            "clauses_analyzed": 0,
        }), 200

    text = text[:10_000_000]

    # --- Sentence splitting ---
    # Split only on sentence-ending punctuation FOLLOWED BY whitespace, or on
    # newlines. The old bare [.!?] split inside emails (privacy.help), section
    # numbers (III.) and abbreviations (e.g.), producing junk fragments.
    sentence_pattern = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'(])|\n+')
    sentences, current_pos = [], 0

    for match in sentence_pattern.finditer(text):
        if match.group() and match.group().strip('\n') == '' or match.group():
            chunk = text[current_pos: match.start()].strip()
            if is_real_clause(chunk):
                sentences.append({'text': chunk, 'start': current_pos, 'end': match.start()})
            current_pos = match.end()

    remainder = text[current_pos:].strip()
    if is_real_clause(remainder):
        sentences.append({'text': remainder, 'start': current_pos, 'end': len(text)})

    # Deduplicate — some pages render the same content multiple times in the DOM
    seen, unique = set(), []
    for s in sentences:
        key = s['text'].strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    sentences = unique

    # --- Summary is built deterministically AFTER analysis (no LLM call) ---
    # Replaces the local Qwen summary, which was slow (~2s) and hallucinated GDPR articles.
    t_summary = 0.0

    # --- Clause classification (cosine) ---
    t_cosine_start = time.time()
    ethical_points, non_ethical_points, unclear_points, highlighted_indices = [], [], [], []

    # Batch-encode ALL clauses in one vectorized call (much faster on CPU than
    # encoding each sentence separately inside the loop — the old bottleneck).
    clause_texts = [s['text'] for s in sentences]
    clause_embeddings = (
        semantic_model.encode(clause_texts, convert_to_tensor=True, batch_size=64)
        if clause_texts else []
    )

    for idx, sent in enumerate(sentences):
        label, confidence, explanation, gdpr_basis, citation_status, sent_emb = classify_clause(
            sent['text'], emb=clause_embeddings[idx]
        )

        gdpr_evidence = ""
        if label != 'unclear' and citation_status in ('supported', 'tentative') and gdpr_basis:
            gdpr_evidence = retrieve_gdpr_evidence(sent_emb)

        point = {
            "text": sent['text'],
            "label": label,  # cosine label, preserved for logging even after list moves
            "confidence": confidence,
            "confidence_label": confidence_tier(confidence),
            "reasoning": explanation,
            "gdpr_basis": gdpr_basis,
            "citation_status": citation_status,
            "gdpr_evidence": gdpr_evidence,
        }
        highlight = {"text": sent['text'], "type": label}

        if label == 'ethical':
            ethical_points.append(point)
            highlighted_indices.append(highlight)
        elif label == 'non_ethical':
            non_ethical_points.append(point)
            highlighted_indices.append(highlight)
        else:
            unclear_points.append(point)

    ethical_points.sort(key=lambda x: x['confidence'], reverse=True)
    non_ethical_points.sort(key=lambda x: x['confidence'], reverse=True)
    unclear_points.sort(key=lambda x: x['confidence'], reverse=True)
    t_cosine = round(time.time() - t_cosine_start, 2)

    # --- LLM verification (Claude Haiku post-filter) ---
    # Candidates come from BOTH lists. The cosine stage can put a protective-looking
    # phrase on the wrong side of the line, and if only the risky side is ever
    # checked, that mistake can never be caught. The verifier is the semantic step,
    # so it gets to see every confident classification, not just one direction.
    t_verify_start = time.time()
    # The verifier is the semantic step, so borderline clauses go to it too. A
    # Medium-confidence clause is exactly the one a reader would be misled by if
    # it were dropped on a cosine score alone. The cosine label only decides
    # which way the verifier is asked to argue, never whether it is asked.
    _candidates = [p for p in non_ethical_points if p['confidence'] >= CONF_VERIFY_MIN][:LLM_VERIFY_MAX_CANDIDATES]
    _protective_candidates = [p for p in ethical_points if p['confidence'] >= CONF_VERIFY_MIN][:LLM_VERIFY_MAX_CANDIDATES]
    _llm_enabled = LLM_VERIFY and LLM_VERIFY_AVAILABLE and bool(_candidates or _protective_candidates)

    if _llm_enabled:
        # One batched call. Candidates carry their cosine label so the verifier
        # knows which direction to argue: 'non_ethical' candidates are questioned,
        # 'ethical' candidates have to earn their protection.
        _batch = _candidates + _protective_candidates
        verdicts = verify_clauses_with_llm(_batch)
        dismissed_texts = set()
        overturned_texts = set()

        for i, point in enumerate(_batch):
            v = verdicts.get(i)
            if v is None:
                continue
            point['llm_concern'] = v['concern']
            point['llm_reason'] = v['reason']
            point['llm_article'] = v['article']
            was_protective = point.get('label') == 'ethical'
            if was_protective:
                # A protective candidate only stays protective if the verifier agrees.
                if v['concern']:
                    point['overturned_by_llm'] = True
                    overturned_texts.add(point['text'])
                    point['gdpr_basis'] = v['article'] or ''
                    point['reasoning'] = v['reason'] or point['reasoning']
                    point['citation_status'] = 'llm_verified'
                else:
                    point['citation_status'] = 'llm_verified'
                    point['reasoning'] = v['reason'] or point['reasoning']
            else:
                if not v['concern']:
                    point['llm_dismissed'] = True
                    dismissed_texts.add(point['text'])
                else:
                    point['gdpr_basis'] = v['article'] or point['gdpr_basis']
                    point['reasoning'] = v['reason'] or point['reasoning']
                    point['citation_status'] = 'llm_verified'
                    # Deterministic children's-data override: if the clause is about
                    # minors, force Art.8 (Haiku tends to mislabel these as Art.6).
                    # Children's-data gaps also signal likely COPPA exposure for US users.
                    if _is_children_clause(point['text']):
                        point['gdpr_basis'] = "Art.8 Children's Consent (+ likely COPPA exposure)"

        # Remove dismissed from non_ethical, move to unclear so nothing is lost
        kept, dismissed_list = [], []
        for p in non_ethical_points:
            if p['text'] in dismissed_texts:
                p['citation_status'] = 'llm_dismissed'
                dismissed_list.append(p)
            else:
                kept.append(p)
        non_ethical_points = kept
        unclear_points.extend(dismissed_list)

        # A protective clause the verifier overturned becomes a flagged one.
        if overturned_texts:
            kept_protective, moved = [], []
            for p in ethical_points:
                if p['text'] in overturned_texts:
                    moved.append(p)
                else:
                    kept_protective.append(p)
            ethical_points = kept_protective
            non_ethical_points.extend(moved)

        # Re-sort: llm_verified first, then by confidence
        non_ethical_points.sort(
            key=lambda x: (0 if x.get('citation_status') == 'llm_verified' else 1, -x['confidence'])
        )
        print(f"  ⏱ LLM verify: dismissed {len(dismissed_texts)}/{len(_candidates)} candidates")

    t_verify = round(time.time() - t_verify_start, 2)
    print(f"  ⏱ Phases — summary: {t_summary}s | cosine: {t_cosine}s | verify: {t_verify}s | total: {round(time.time()-t_start,2)}s")

    # --- Risk level ---
    total_classified = len(ethical_points) + len(non_ethical_points)
    if total_classified == 0:
        risk_level = "Unknown"
    else:
        risk_ratio = len(non_ethical_points) / total_classified
        avg_non_conf = (
            sum(p['confidence'] for p in non_ethical_points) / len(non_ethical_points)
            if non_ethical_points else 0
        )
        if (risk_ratio >= 0.5 and avg_non_conf > 0.65) or avg_non_conf > 0.75:
            risk_level = "High"
        elif risk_ratio >= 0.35 or avg_non_conf > 0.60:
            risk_level = "Medium"
        else:
            risk_level = "Low"

    # --- Deterministic summary (assembled from results — fast, cannot hallucinate) ---
    verified = [p for p in non_ethical_points if p.get('citation_status') == 'llm_verified']
    shown_concerns = [p for p in non_ethical_points if p['confidence_label'] != 'Low']
    if shown_concerns:
        summary = (
            f"{risk_level} risk: {len(shown_concerns)} potential concern(s) flagged across "
            f"{len(sentences)} clauses for human review."
        )
        if verified:
            # Deduplicate. The verifier often gives several clauses the same
            # short reason, and repeating it three times reads as a broken
            # string rather than as three separate findings.
            seen_reasons, tops_list = set(), []
            for p in verified:
                r = (p.get('reasoning') or '').strip()
                if not r:
                    continue
                key = r.lower().rstrip('.')
                if key in seen_reasons:
                    continue
                seen_reasons.add(key)
                tops_list.append(r)
                if len(tops_list) == 3:
                    break
            tops = "; ".join(tops_list)
            if tops:
                summary += f" Key items — {tops}."
    else:
        summary = (
            f"{risk_level} risk: no high-confidence concerns flagged across "
            f"{len(sentences)} clauses. Review the source terms to confirm."
        )

    # --- Link extraction ---
    found_urls = re.findall(r'https?://[^\s\)]+', text)
    all_links = list(set(found_urls))
    for link in incoming_links:
        if link.get('url') and link['url'] not in all_links:
            all_links.append(link['url'])

    # --- Feedback logging (data flywheel — never blocks the response) ---
    if FEEDBACK_LOGGING:
        try:
            feedback_log.log_scan(
                {
                    "source_url": data.get("url", ""),
                    "total_chars": len(text),
                    "clauses_analyzed": len(sentences),
                    "risk_level": risk_level,
                    "summary": summary,
                    "llm_verification_enabled": _llm_enabled,
                    "phase_timings": {"summary_s": t_summary, "cosine_s": t_cosine, "verify_s": t_verify},
                },
                ethical_points, non_ethical_points, unclear_points, _LLM_PROMPT_VERSION,
            )
        except Exception as _log_exc:
            print(f"  ⚠️  logging hook error: {_log_exc}")

    return jsonify({
        "ethical_points": ethical_points,
        "non_ethical_points": non_ethical_points,
        "unclear_points": unclear_points,
        "risk_level": risk_level,
        "summary": summary,
        "processing_time_seconds": round(time.time() - t_start, 2),
        "clauses_analyzed": len(sentences),
        "highlighted_indices": highlighted_indices,
        "total_text_analyzed_chars": len(text),
        "links": all_links,
        "links_count": len(all_links),
        "gdpr_rag_enabled": gdpr_kb_index is not None,
        "llm_verification_enabled": _llm_enabled,
        "generation_model": "none (deterministic summary)",
        "verification_model": _LLM_MODEL if _llm_enabled else "none",
        "classification_method": "gdpr_semantic_cosine_similarity + llm_verify" if _llm_enabled else "gdpr_semantic_cosine_similarity",
        "phase_timings": {"summary_s": t_summary, "cosine_s": t_cosine, "verify_s": t_verify},
        "analysis_coverage": "Entire page — no chunks skipped",
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "running",
        "port": 5001,
        "models_loaded": True,
        "generation_model": "none (deterministic summary)",
        "verification_model": _LLM_MODEL if LLM_VERIFY_AVAILABLE else "none",
        "gdpr_rag_enabled": gdpr_kb_index is not None,
        "gdpr_kb_vectors": gdpr_kb_index.ntotal if gdpr_kb_index else 0,
    })


if __name__ == '__main__':
    # Sanity check — protective clauses must NOT be flagged non_ethical
    _sanity = [
        ("nothing herein will be deemed to waive preclude or limit our rights", False),
        ("These terms are in no way intended to restrict those rights", False),
        ("you may have certain rights that can't be limited by a contract", False),
        ("nothing will affect your statutory rights of a consumer", False),
        ("We may sell your data to third-party partners", True),
        ("we are not liable for any data breaches", True),
        ("class action waiver applies to all disputes", True),
    ]
    print("\n=== Protective clause sanity check ===")
    all_pass = True
    for _text, _expect_non in _sanity:
        _label, _conf, _, _, _, _ = classify_clause(_text)
        _ok = (_label == "non_ethical") == _expect_non
        if not _ok:
            all_pass = False
        print(f"  {'✅' if _ok else '❌'} [{_label:12s}] {_text[:65]}")
    print(f"  {'All pass ✅' if all_pass else 'Some failed ❌ — adjust CONF thresholds or PROTECTIVE_PHRASES'}")
    print("======================================\n")
    app.run(port=5001, debug=False, threaded=True)
