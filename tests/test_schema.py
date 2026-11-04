import pytest

from leadbot.ghl.schema import (
    SchemaError, load_schema, niche_tag, slug, tier_tag,
)


@pytest.fixture(scope="module")
def schema():
    return load_schema()


def test_eighteen_fields_seven_built_in_ui(schema):
    assert len(schema.fields) == 18
    ui = [f.name for f in schema.fields if f.created_by == "ui"]
    assert ui == ["Lead Tier", "Niche", "Primary Signal", "All Signals",
                  "GBP Status", "Meta Ads Active", "Call Outcome"]


def test_tiers_niches_categories_come_from_field_options(schema):
    assert schema.tiers == ("Hot", "Warm", "Cold")
    assert schema.niches[2] == "HVAC"
    assert schema.categories == ("New Biz", "GBP Suspended", "Broken Site", "Ad Waste", "Rank Gap")


def test_slug_and_tags():
    assert slug("Counselors / therapists") == "counselors-therapists"
    assert niche_tag("Med spas / dental") == "niche-med-spas-dental"
    assert tier_tag("Hot") == "tier-hot"


def test_all_tags_has_static_tier_niche_and_signal_tags_once(schema):
    tags = schema.all_tags()
    assert len(tags) == len(set(tags))
    for t in ["bot-lead", "bot-try-3", "tier-cold", "niche-hvac", "sig-rank"]:
        assert t in tags
    assert len(tags) == 7 + 3 + 6 + 5


def test_field_lookup_and_unknown_field(schema):
    assert schema.field("Rating").data_type == "FLOAT"
    with pytest.raises(KeyError):
        schema.field("Nope")


def test_entry_stage_must_be_a_stage(tmp_path):
    bad = tmp_path / "s.toml"
    bad.write_text(
        '[pipeline]\nname = "P"\nstages = ["A"]\nentry_stage = "B"\n'
        '[duplicate]\nclient_tags = []\nlost_window_days = 90\n'
        '[tags]\nstatic = []\n[signals]\n[signal_tags]\n', encoding="utf-8")
    with pytest.raises(SchemaError, match="entry_stage"):
        load_schema(bad)


def test_primary_signal_options_must_match_signal_categories(tmp_path):
    bad = tmp_path / "s.toml"
    bad.write_text(
        '[pipeline]\nname = "P"\nstages = ["New"]\nentry_stage = "New"\n'
        '[duplicate]\nclient_tags = []\nlost_window_days = 90\n'
        '[tags]\nstatic = []\n'
        '[signals]\nnew_business = "New Biz"\n'
        '[signal_tags]\n"New Biz" = "sig-new"\n'
        '[[fields]]\nname = "Lead Tier"\ndata_type = "SINGLE_OPTIONS"\nsource = "lead_tier"\noptions = ["Hot"]\n'
        '[[fields]]\nname = "Niche"\ndata_type = "SINGLE_OPTIONS"\nsource = "niche"\noptions = ["HVAC"]\n'
        '[[fields]]\nname = "Primary Signal"\ndata_type = "SINGLE_OPTIONS"\nsource = "primary_signal"\noptions = ["Other"]\n'
        '[[fields]]\nname = "All Signals"\ndata_type = "MULTIPLE_OPTIONS"\nsource = "signals"\noptions = ["New Biz"]\n',
        encoding="utf-8")
    with pytest.raises(SchemaError, match="Primary Signal"):
        load_schema(bad)
