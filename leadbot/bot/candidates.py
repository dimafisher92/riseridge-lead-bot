"""Candidate and qualified records (spec 3.7, 3.8)."""

import json
import os

from leadbot.bot.score import SIGNAL_ORDER
from leadbot.bot.signals import TRUE, UNKNOWN

EXTRA_KEYS = ("evidence", "unknown", "place_id")
# Claude may only fill the two text fields; everything here must come back unchanged.
LOCKED_KEYS = ("business_name", "phone", "niche", "signals", "primary_signal", "lead_score",
               "lead_tier", "bot_run_id")
MAX_SUMMARY_LINES = 3
MAX_SUMMARY_CHARS = 400
MAX_OPENER_WORDS = 30


class QualifiedError(ValueError):
    pass


def build_candidate(place, signals, *, score, tier, primary, plan, bot_run_id, email,
                    gbp_status, rank):
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
        "evidence": {k: s.evidence for k, s in signals.items() if s.state != UNKNOWN},
        "unknown": [s for s in SIGNAL_ORDER if signals[s].state == UNKNOWN],
        "place_id": place.place_id,
    }


def needs_text(record):
    return record["lead_tier"] in ("Hot", "Warm") or bool(record.get("email"))


def to_payload(record):
    return {k: v for k, v in record.items() if k not in EXTRA_KEYS}


def check_qualified(qualified, candidate):
    run_id = candidate["bot_run_id"]
    for key in LOCKED_KEYS:
        if qualified.get(key) != candidate.get(key):
            raise QualifiedError(f"{run_id}: {key} changed")
    summary = (qualified.get("audit_summary") or "").strip()
    if not summary:
        raise QualifiedError(f"{run_id}: audit_summary is empty")
    if len(summary.splitlines()) > MAX_SUMMARY_LINES or len(summary) > MAX_SUMMARY_CHARS:
        raise QualifiedError(f"{run_id}: audit_summary over {MAX_SUMMARY_LINES} lines "
                             f"or {MAX_SUMMARY_CHARS} characters")
    opener = (qualified.get("call_opener") or "").strip()
    if not opener:
        raise QualifiedError(f"{run_id}: call_opener is empty")
    if len(opener.split()) > MAX_OPENER_WORDS:
        raise QualifiedError(f"{run_id}: call_opener over {MAX_OPENER_WORDS} words")


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
