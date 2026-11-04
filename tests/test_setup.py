import pytest

from leadbot.ghl.schema import load_schema
from leadbot.ghl.setup import run_setup
from tests.fakes import FakeClient


@pytest.fixture
def schema():
    return load_schema()


def add_ui_fields(client, schema):
    for f in schema.fields:
        if f.created_by == "ui":
            client.fields.append({"id": "ui-" + f.name, "name": f.name,
                                  "dataType": f.data_type, "picklistOptions": list(f.options)})


def test_fresh_location_creates_tags_api_fields_pipeline_and_lists_ui_fields(schema):
    client = FakeClient()
    report = run_setup(client, schema)
    assert len([c for c in client.calls if c[0] == "create_tag"]) == len(schema.all_tags())
    assert len([c for c in client.calls if c[0] == "create_custom_field"]) == 11
    assert ("create_pipeline", "Bot Leads – SEO", schema.stages) in client.calls
    assert report.missing_ui == ["Lead Tier", "Niche", "Primary Signal", "All Signals",
                                 "GBP Status", "Meta Ads Active", "Call Outcome"]
    assert not report.ok


def test_second_run_creates_nothing_and_is_ok_once_ui_fields_exist(schema):
    client = FakeClient()
    run_setup(client, schema)
    add_ui_fields(client, schema)
    client.calls.clear()
    report = run_setup(client, schema)
    assert client.calls == []
    assert report.ok
    assert report.created == []


def test_existing_tag_matched_case_insensitively(schema):
    client = FakeClient()
    client.tags.append({"id": "t0", "name": "Bot-Lead"})
    run_setup(client, schema)
    assert ("create_tag", "bot-lead") not in client.calls


def test_wrong_type_and_missing_option_are_drift_not_changes(schema):
    client = FakeClient()
    add_ui_fields(client, schema)
    client.fields.append({"id": "x", "name": "Lead Score", "dataType": "TEXT", "picklistOptions": []})
    tier = next(f for f in client.fields if f["name"] == "Lead Tier")
    tier["picklistOptions"] = ["Hot", "Warm"]
    report = run_setup(client, schema)
    assert any("Lead Score" in d and "TEXT" in d for d in report.drift)
    assert any("Lead Tier" in d and "Cold" in d for d in report.drift)
    assert ("create_custom_field", "Lead Score", "NUMERICAL") not in client.calls


def test_pipeline_with_different_stages_is_drift(schema):
    client = FakeClient()
    client.pipelines.append({"id": "p0", "name": "Bot Leads – SEO",
                             "stages": [{"id": "s0", "name": "New"}]})
    report = run_setup(client, schema)
    assert any("stages" in d for d in report.drift)
    assert not any(c[0] == "create_pipeline" for c in client.calls)


def test_dry_run_and_verify_only_change_nothing(schema):
    for kwargs in ({"dry_run": True}, {"verify_only": True}):
        client = FakeClient()
        report = run_setup(client, schema, **kwargs)
        assert client.calls == []
    assert run_setup(FakeClient(), schema, dry_run=True).created  # lists would-create
