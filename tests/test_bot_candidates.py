import pytest

from leadbot.bot.candidates import QualifiedError, merge_qualified, needs_text, to_payload

BASE = {"business_name": "Bright Path Counseling (fictional)", "phone": "+15125550132",
        "email": None, "niche": "Counselors / therapists", "signals": ["new_business"],
        "primary_signal": "New Biz", "lead_score": 20, "lead_tier": "Cold",
        "audit_summary": "", "call_opener": "", "bot_run_id": "r-0001",
        "evidence": {"new_business": "4 reviews"}, "unknown": [], "place_id": "fx-1",
        "negatives": ["inactive_listing"], "notes": {"rank_gap": "not in the top 60"}}
SUMMARY = "Only 4 reviews, all recent."
OPENER = "Hi, congrats on opening Bright Path Counseling."


def texts(**over):
    q = {"bot_run_id": "r-0001", "audit_summary": SUMMARY, "call_opener": OPENER}
    q.update(over)
    return q


def test_needs_text():
    assert needs_text(dict(BASE, lead_tier="Hot"))
    assert needs_text(dict(BASE, lead_tier="Warm"))
    assert not needs_text(BASE)
    assert needs_text(dict(BASE, email="hi@x.example.com"))


def test_to_payload_drops_extra_keys():
    payload = to_payload(BASE)
    assert "evidence" not in payload and "place_id" not in payload and "unknown" not in payload
    assert "negatives" not in payload and "notes" not in payload
    assert payload["bot_run_id"] == "r-0001"


def test_merge_returns_candidate_with_the_two_texts():
    merged = merge_qualified(texts(), BASE)
    assert merged == dict(BASE, audit_summary=SUMMARY, call_opener=OPENER)
    assert BASE["audit_summary"] == "" and BASE["call_opener"] == ""  # candidate untouched


def test_merge_strips_texts():
    merged = merge_qualified(texts(audit_summary="  Line one.\nLine two.  ", call_opener=f" {OPENER} "),
                             BASE)
    assert merged["audit_summary"] == "Line one.\nLine two."
    assert merged["call_opener"] == OPENER


@pytest.mark.parametrize("over,needle", [
    ({"audit_summary": ""}, "audit_summary"), ({"call_opener": " "}, "call_opener"),
    ({"audit_summary": "a\nb\nc\nd"}, "audit_summary"),
    ({"audit_summary": "x" * 401}, "audit_summary"),
    ({"call_opener": " ".join(["word"] * 30)}, "call_opener"),
    ({"audit_summary": 5}, "audit_summary"), ({"call_opener": ["Hi"]}, "call_opener")])
def test_invalid_texts_rejected(over, needle):
    with pytest.raises(QualifiedError, match=needle):
        merge_qualified(texts(**over), BASE)


def test_missing_text_rejected():
    q = texts()
    del q["call_opener"]
    with pytest.raises(QualifiedError, match="call_opener is empty"):
        merge_qualified(q, BASE)


def test_opener_of_29_words_passes():
    merge_qualified(texts(call_opener=" ".join(["word"] * 29)), BASE)


@pytest.mark.parametrize("key", ["lead_score", "lead_tier", "signals", "phone", "email",
                                 "website", "business_name", "note"])
def test_any_other_key_rejected(key):
    with pytest.raises(QualifiedError, match=f"unexpected field {key}"):
        merge_qualified(texts(**{key: BASE.get(key, "x")}), BASE)


def test_other_bot_run_id_rejected():
    with pytest.raises(QualifiedError, match="bot_run_id"):
        merge_qualified(texts(bot_run_id="r-0002"), BASE)
