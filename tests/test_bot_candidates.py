import pytest

from leadbot.bot.candidates import QualifiedError, check_qualified, needs_text, to_payload

BASE = {"business_name": "Bright Path Counseling (fictional)", "phone": "+15125550132",
        "email": None, "niche": "Counselors / therapists", "signals": ["new_business"],
        "primary_signal": "New Biz", "lead_score": 20, "lead_tier": "Cold",
        "audit_summary": "", "call_opener": "", "bot_run_id": "r-0001",
        "evidence": {"new_business": "4 reviews"}, "unknown": [], "place_id": "fx-1"}


def filled(**over):
    q = dict(BASE, audit_summary="Only 4 reviews, all recent.",
             call_opener="Hi, congrats on opening Bright Path Counseling.")
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
    assert payload["bot_run_id"] == "r-0001"


def test_valid_qualified_passes():
    check_qualified(filled(), BASE)


@pytest.mark.parametrize("over,needle", [
    ({"lead_score": 90}, "lead_score"), ({"lead_tier": "Hot"}, "lead_tier"),
    ({"signals": []}, "signals"), ({"phone": "+15125550199"}, "phone"),
    ({"audit_summary": ""}, "audit_summary"), ({"call_opener": " "}, "call_opener"),
    ({"audit_summary": "a\nb\nc\nd"}, "audit_summary"),
    ({"call_opener": " ".join(["word"] * 31)}, "call_opener"),
    ({"email": "other@x.example.com"}, "email"), ({"website": "https://evil.example.com/"}, "website")])
def test_invalid_qualified_rejected(over, needle):
    with pytest.raises(QualifiedError, match=needle):
        check_qualified(filled(**over), BASE)


def test_changed_website_rejected_when_candidate_has_one():
    candidate = dict(BASE, website="https://brightpath.example.com/")
    qualified = filled(website="https://evil.example.com/")
    with pytest.raises(QualifiedError, match="website changed"):
        check_qualified(qualified, candidate)


def test_added_field_rejected():
    with pytest.raises(QualifiedError, match="unexpected field note"):
        check_qualified(filled(note="x"), BASE)
