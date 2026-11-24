"""Candidate and qualified records (spec 3.7, 3.8)."""

import codecs
import json
import os

from leadbot.bot.score import SIGNAL_ORDER
from leadbot.bot.signals import TRUE, UNKNOWN

EXTRA_KEYS = ("evidence", "unknown", "place_id", "negatives", "notes")
# Claude writes only these two text fields; a qualified line carries them plus bot_run_id.
TEXT_FIELDS = ("audit_summary", "call_opener")
QUALIFIED_KEYS = ("bot_run_id",) + TEXT_FIELDS
MAX_SUMMARY_LINES = 3
MAX_SUMMARY_CHARS = 400
MAX_OPENER_WORDS = 30  # the opener must be under this many words


class QualifiedError(ValueError):
    pass


def build_candidate(place, signals, *, score, tier, primary, plan, bot_run_id, email,
                    gbp_status, rank, negatives=None):
    negatives = negatives or {}
    true_negatives = [k for k, s in negatives.items() if s.state == TRUE]
    evidence = {k: s.evidence for k, s in signals.items() if s.state != UNKNOWN}
    evidence.update({k: negatives[k].evidence for k in true_negatives})
    # UNKNOWN rank/competitor evidence is context for the rep, never a problem signal.
    notes = {k: signals[k].evidence for k in ("rank_gap", "competitor_gap")
             if signals[k].state == UNKNOWN}
    return {
        "business_name": place.name,
        "phone": place.phone,
        "email": email,
        "website": place.website,
        "niche": plan.niche,
        "city": plan.city,
        "gbp_url": place.gbp_url,
        "gbp_status": gbp_status,
        "review_count": place.review_count,
        "rating": place.rating,
        "money_keyword": plan.money_keyword,
        "current_rank": rank.position,
        "signals": [s for s in SIGNAL_ORDER if signals[s].state == TRUE],
        "primary_signal": primary,
        "lead_score": score,
        "lead_tier": tier,
        "audit_summary": "",
        "call_opener": "",
        "bot_run_id": bot_run_id,
        "evidence": evidence,
        "unknown": [s for s in SIGNAL_ORDER if signals[s].state == UNKNOWN],
        "place_id": place.place_id,
        "negatives": true_negatives,
        "notes": notes,
    }


def needs_text(record):
    return record["lead_tier"] in ("Hot", "Warm") or bool(record.get("email"))


def to_payload(record):
    return {k: v for k, v in record.items() if k not in EXTRA_KEYS}


def merge_qualified(qualified, candidate):
    """Check one qualified.jsonl line ({bot_run_id, audit_summary, call_opener}) and return
    the candidate with the two texts filled in. Every other field comes from the candidate."""
    run_id = candidate["bot_run_id"]
    if not isinstance(qualified, dict):
        raise QualifiedError(f"{run_id}: line is not a JSON object")
    for key in qualified:
        if key not in QUALIFIED_KEYS:
            raise QualifiedError(f"{run_id}: unexpected field {key}")
    if qualified.get("bot_run_id") != run_id:
        raise QualifiedError(f"{run_id}: bot_run_id does not match")
    texts = {}
    for key in TEXT_FIELDS:
        value = qualified.get(key)
        if value is not None and not isinstance(value, str):
            raise QualifiedError(f"{run_id}: {key} is not text")
        texts[key] = (value or "").strip()
    summary, opener = texts["audit_summary"], texts["call_opener"]
    if not summary:
        raise QualifiedError(f"{run_id}: audit_summary is empty")
    if len(summary.splitlines()) > MAX_SUMMARY_LINES or len(summary) > MAX_SUMMARY_CHARS:
        raise QualifiedError(f"{run_id}: audit_summary over {MAX_SUMMARY_LINES} lines "
                             f"or {MAX_SUMMARY_CHARS} characters")
    if not opener:
        raise QualifiedError(f"{run_id}: call_opener is empty")
    if len(opener.split()) >= MAX_OPENER_WORDS:
        raise QualifiedError(f"{run_id}: call_opener must be under {MAX_OPENER_WORDS} words")
    return dict(candidate, **texts)


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_jsonl_lenient(path):
    """Yield (line number, record) for each non-blank line; record is None when the line is
    not valid JSON, not valid UTF-8 or not a JSON object. Used for qualified.jsonl, which
    Claude writes, so one bad line never loses the others."""
    if not os.path.exists(path):
        return
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            if n == 1:
                raw = raw.removeprefix(codecs.BOM_UTF8)
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except ValueError:  # JSONDecodeError and UnicodeDecodeError
                record = None
            yield n, record if isinstance(record, dict) else None
