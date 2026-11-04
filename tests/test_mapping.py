import pytest

from leadbot.ghl.mapping import (
    SAMPLE_PAYLOAD, PayloadError, contact_body, field_values, lead_tags,
    primary_category, signal_categories, validate_payload,
)
from leadbot.ghl.schema import load_schema


@pytest.fixture(scope="module")
def schema():
    return load_schema()


@pytest.fixture
def ids(schema):
    return {f.name: "id-" + f.name for f in schema.fields}


def payload(**changes):
    p = dict(SAMPLE_PAYLOAD)
    p.update(changes)
    return {k: v for k, v in p.items() if v is not ...}


def test_sample_payload_is_valid(schema):
    validate_payload(SAMPLE_PAYLOAD, schema)


@pytest.mark.parametrize("changes, msg", [
    ({"business_name": ""}, "business_name"),
    ({"phone": ..., "email": ...}, "phone or email"),
    ({"lead_tier": "Lukewarm"}, "lead_tier"),
    ({"niche": "Plumbing"}, "niche"),
    ({"bot_run_id": ...}, "bot_run_id"),
    ({"signals": ["made_up"]}, "made_up"),
    ({"primary_signal": "Something"}, "primary_signal"),
])
def test_invalid_payloads(schema, changes, msg):
    with pytest.raises(PayloadError, match=msg):
        validate_payload(payload(**changes), schema)


def test_signal_categories_dedupe_in_order(schema):
    p = payload(signals=["google_ads_spend", "meta_ads_active", "broken_website"])
    assert signal_categories(p, schema) == ["Ad Waste", "Broken Site"]


def test_primary_category_accepts_label_or_flag(schema):
    assert primary_category(payload(primary_signal="GBP Suspended"), schema) == "GBP Suspended"
    assert primary_category(payload(primary_signal="rank_gap"), schema) == "Rank Gap"
    assert primary_category(payload(primary_signal=...), schema) is None


def test_field_values_skip_missing_and_never_call_outcome(schema):
    values = field_values(payload(current_rank=None, gbp_url=""), schema)
    assert "Current Rank" not in values
    assert "GBP URL" not in values
    assert "Call Outcome" not in values
    assert values["Lead Score"] == 82
    assert values["All Signals"] == ["GBP Suspended", "Ad Waste", "Broken Site"]
    assert values["Meta Ads Active"] == ["Yes"]
    assert field_values(payload(meta_ads_active=False), schema)["Meta Ads Active"] == []
    assert "Meta Ads Active" not in field_values(payload(meta_ads_active=None), schema)


def test_contact_body_has_no_tags_and_uses_field_ids(schema, ids):
    body = contact_body(SAMPLE_PAYLOAD, schema, ids, "LOC1")
    assert "tags" not in body
    assert body["locationId"] == "LOC1"
    assert body["companyName"] == body["name"] == "Lone Star Cooling"
    assert (body["city"], body["state"], body["country"]) == ("Austin", "TX", "US")
    assert body["source"] == "Lead Bot"
    assert {"id": "id-Lead Tier", "fieldValue": "Hot"} in body["customFields"]


def test_contact_body_omits_missing_email(schema, ids):
    body = contact_body(payload(email=...), schema, ids, "LOC1")
    assert "email" not in body


def test_lead_tags(schema):
    assert lead_tags(SAMPLE_PAYLOAD, schema) == [
        "bot-lead", "tier-hot", "niche-hvac", "sig-gbp", "sig-ads", "sig-site"]
