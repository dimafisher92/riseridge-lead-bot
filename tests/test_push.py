import datetime as dt
from dataclasses import replace

import pytest

from leadbot.ghl.client import GHLAuthError, GHLError
from leadbot.ghl.mapping import SAMPLE_PAYLOAD
from leadbot.ghl.push import SetupIncomplete, load_context, push_lead
from leadbot.ghl.schema import load_schema
from leadbot.ghl.setup import run_setup
from tests.fakes import FakeClient

TODAY = dt.date(2026, 11, 4)


@pytest.fixture
def schema():
    return replace(load_schema(), client_tags=("client",))


@pytest.fixture
def client(schema):
    c = FakeClient()
    run_setup(c, schema)
    for f in schema.fields:
        if f.created_by == "ui":
            c.fields.append({"id": "ui-" + f.name, "name": f.name, "dataType": f.data_type,
                             "picklistOptions": list(f.options)})
    return c


@pytest.fixture
def ctx(client, schema):
    return load_context(client, schema)


def lead(**changes):
    p = dict(SAMPLE_PAYLOAD)
    p.update(changes)
    return p


def test_load_context_reports_missing_setup(schema):
    with pytest.raises(SetupIncomplete, match="Lead Score"):
        load_context(FakeClient(), schema)


def test_load_context_reports_missing_entry_stage(client, schema):
    for p in client.pipelines:
        p["stages"] = [s for s in p["stages"] if s["name"] != "New"]
    with pytest.raises(SetupIncomplete, match="stage New"):
        load_context(client, schema)


def test_hot_lead_creates_contact_tags_and_opportunity_in_new(client, schema, ctx):
    r = push_lead(client, schema, ctx, lead(), extra_tags=("bot-test",), today=TODAY)
    assert r.outcome == "created"
    contact = client.contacts[r.contact_id]
    assert contact["tags"] == ["bot-lead", "tier-hot", "niche-hvac", "sig-gbp", "sig-ads",
                               "sig-site", "bot-test"]
    opp = client.opportunities[r.opportunity_id]
    assert opp["pipelineId"] == ctx.pipeline_id
    assert opp["pipelineStageId"] == ctx.stage_ids["New"]
    assert opp["status"] == "open"
    assert opp["name"] == "Lone Star Cooling – HVAC"


def test_upsert_never_sends_tags_or_call_outcome(client, schema, ctx):
    push_lead(client, schema, ctx, lead(), today=TODAY)
    body = next(c[1] for c in client.calls if c[0] == "upsert_contact")
    assert "tags" not in body
    assert ctx.field_ids["Call Outcome"] not in [cf["id"] for cf in body["customFields"]]


def test_cold_lead_gets_tags_but_no_opportunity(client, schema, ctx):
    r = push_lead(client, schema, ctx, lead(lead_tier="Cold", lead_score=30), today=TODAY)
    assert r.outcome == "created"
    assert r.opportunity_id is None
    assert "tier-cold" in client.contacts[r.contact_id]["tags"]
    assert client.opportunities == {}


def test_repush_of_open_lead_is_existing_and_creates_no_second_opportunity(client, schema, ctx):
    first = push_lead(client, schema, ctx, lead(), today=TODAY)
    again = push_lead(client, schema, ctx, lead(lead_score=90), today=TODAY)
    assert again.outcome == "existing"
    assert again.opportunity_id == first.opportunity_id
    assert len(client.opportunities) == 1


def test_repush_of_cold_lead_is_updated(client, schema, ctx):
    push_lead(client, schema, ctx, lead(lead_tier="Cold"), today=TODAY)
    assert push_lead(client, schema, ctx, lead(lead_tier="Cold"), today=TODAY).outcome == "updated"


def test_existing_client_is_duplicate(client, schema, ctx):
    contact, _ = client.upsert_contact({"locationId": "LOC1", "phone": SAMPLE_PAYLOAD["phone"]})
    client.add_tags(contact["id"], ["client"])
    r = push_lead(client, schema, ctx, lead(), today=TODAY)
    assert r.outcome == "duplicate"
    assert client.contacts[contact["id"]]["tags"] == ["client", "bot-dupe"]
    assert client.opportunities == {}


def test_lost_within_90_days_is_duplicate_but_older_is_not(client, schema, ctx):
    contact, _ = client.upsert_contact({"locationId": "LOC1", "phone": SAMPLE_PAYLOAD["phone"]})
    client.create_opportunity({"contactId": contact["id"], "pipelineId": "other", "status": "lost",
                               "lastStatusChangeAt": "2026-09-01T10:00:00.000Z"})
    assert push_lead(client, schema, ctx, lead(), today=TODAY).outcome == "duplicate"
    for o in client.opportunities.values():
        o["lastStatusChangeAt"] = "2026-07-01T10:00:00.000Z"
    client.contacts[contact["id"]]["tags"] = []
    r = push_lead(client, schema, ctx, lead(), today=TODAY)
    # Contact exists, so the upsert is an update; the old lost deal sits in
    # another pipeline, so a new open opportunity is created.
    assert r.outcome == "updated"
    assert r.opportunity_id is not None


def test_invalid_payload_is_rejected_without_calls(client, schema, ctx):
    client.calls.clear()
    r = push_lead(client, schema, ctx, lead(phone=None, email=None), today=TODAY)
    assert r.outcome == "rejected"
    assert "phone or email" in r.message
    assert client.calls == []


def test_ghl_error_after_contact_write_is_failed_with_contact_id(client, schema, ctx):
    def boom(body):
        raise GHLError(422, "pipelineStageId invalid")
    client.create_opportunity = boom
    r = push_lead(client, schema, ctx, lead(), today=TODAY)
    assert r.outcome == "failed"
    assert r.contact_id is not None
    assert "pipelineStageId invalid" in r.message


def test_auth_error_stops_the_batch(client, schema, ctx):
    def denied(phone, email):
        raise GHLAuthError(403, "missing scope")
    client.find_duplicate = denied
    with pytest.raises(GHLAuthError):
        push_lead(client, schema, ctx, lead(), today=TODAY)
