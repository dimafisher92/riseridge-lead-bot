from collections import Counter

from leadbot.ghl.mapping import PayloadError, validate_payload
from leadbot.ghl.schema import load_schema
from leadbot.ghl.testdata import (
    EXPECTED_FIRST_RUN, EXPECTED_REPEAT_RUN, SEEDS, TEST_LEADS,
)


def test_twenty_leads_with_spec_distribution():
    assert len(TEST_LEADS) == 20
    tiers = Counter(l["lead_tier"] for l in TEST_LEADS[:16])
    assert tiers == {"Hot": 6, "Warm": 6, "Cold": 4}
    assert {l["niche"] for l in TEST_LEADS[:6]} == set(load_schema().niches)


def test_all_test_contacts_are_fictional():
    for l in TEST_LEADS:
        if l.get("phone"):
            assert l["phone"].startswith("+151255501")
        if l.get("email"):
            assert l["email"].endswith("@example.com")
    assert SEEDS["client"]["phone"].startswith("+1512555019")


def test_validity_matches_expectations():
    schema = load_schema()
    rejected = 0
    for l in TEST_LEADS:
        try:
            validate_payload(l, schema)
        except PayloadError:
            rejected += 1
    assert rejected == EXPECTED_FIRST_RUN["rejected"] == 1


def test_duplicates_use_seed_phones_and_email_only_lead_exists():
    phones = [l.get("phone") for l in TEST_LEADS]
    assert SEEDS["client"]["phone"] in phones
    assert SEEDS["lost"]["phone"] in phones
    assert any(not l.get("phone") and l.get("email") for l in TEST_LEADS)


def test_expected_counts_add_up():
    assert sum(EXPECTED_FIRST_RUN.values()) == 20
    assert sum(EXPECTED_REPEAT_RUN.values()) == 20
