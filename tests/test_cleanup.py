import pytest

from leadbot.ghl.client import GHLError
from leadbot.ghl.cleanup import cleanup_test_data, find_collisions
from leadbot.ghl.testdata import SEEDS, TEST_LEADS
from tests.fakes import FakeClient

PHONE = TEST_LEADS[0]["phone"]


def _contact(c, phone, tags=(), email=None):
    body = {"locationId": c.location_id, "country": "US", "phone": phone}
    if email:
        body["email"] = email
    contact, _ = c.upsert_contact(body)
    if tags:
        c.add_tags(contact["id"], list(tags))
    return contact["id"]


def _opp(c, cid):
    return c.create_opportunity({"pipelineId": "P1", "pipelineStageId": "S1",
                                 "locationId": c.location_id, "name": "x",
                                 "status": "open", "contactId": cid})["id"]


def _ledger(contacts=(), opportunities=()):
    return {"contacts": list(contacts), "opportunities": list(opportunities), "seeds": {}}


def test_ledger_contact_and_opportunity_fully_removed():
    c = FakeClient()
    cid = _contact(c, "+15125550999", tags=["bot-test"])
    oid = _opp(c, cid)
    result = cleanup_test_data(c, _ledger([cid], [oid]))
    assert c.contacts == {} and c.opportunities == {}
    assert result.contacts_deleted == [cid]
    assert result.opportunities_deleted == [oid]
    assert result.skipped == [] and result.clean is True


def test_bot_test_contact_outside_ledger_found_by_test_phone():
    c = FakeClient()
    cid = _contact(c, PHONE, tags=["bot-test"])
    oid = _opp(c, cid)
    result = cleanup_test_data(c, _ledger())
    assert cid not in c.contacts and oid not in c.opportunities
    assert result.contacts_deleted == [cid] and result.clean is True


def test_untagged_contact_sharing_test_phone_is_skipped_and_kept():
    c = FakeClient()
    cid = _contact(c, PHONE)
    oid = _opp(c, cid)
    result = cleanup_test_data(c, _ledger([cid], [oid]))
    assert cid in c.contacts and oid in c.opportunities
    assert result.skipped == [cid]
    assert result.contacts_deleted == [] and result.opportunities_deleted == []
    assert result.clean is True


def test_delete_opportunity_404_tolerated(monkeypatch):
    c = FakeClient()
    cid = _contact(c, PHONE, tags=["bot-test"])
    oid = _opp(c, cid)

    def gone_then_404(opportunity_id):
        c.opportunities.pop(opportunity_id, None)
        raise GHLError(404, "not found")

    monkeypatch.setattr(c, "delete_opportunity", gone_then_404)
    result = cleanup_test_data(c, _ledger([cid], [oid]))
    assert cid not in c.contacts and result.clean is True


def test_delete_opportunity_400_propagates(monkeypatch):
    c = FakeClient()
    cid = _contact(c, PHONE, tags=["bot-test"])
    oid = _opp(c, cid)

    def refuse(opportunity_id):
        raise GHLError(400, "bad request")

    monkeypatch.setattr(c, "delete_opportunity", refuse)
    with pytest.raises(GHLError) as exc:
        cleanup_test_data(c, _ledger([cid], [oid]))
    assert exc.value.status == 400
    assert cid in c.contacts and oid in c.opportunities


def test_surviving_opportunity_is_a_leftover(monkeypatch):
    c = FakeClient()
    cid = _contact(c, PHONE, tags=["bot-test"])
    oid = _opp(c, cid)
    monkeypatch.setattr(c, "delete_opportunity", lambda opportunity_id: None)
    result = cleanup_test_data(c, _ledger([cid], [oid]))
    assert oid in c.opportunities
    assert result.leftovers and any(oid in line for line in result.leftovers)
    assert result.clean is False


def test_delete_contact_404_tolerated(monkeypatch):
    c = FakeClient()
    cid = _contact(c, PHONE, tags=["bot-test"])

    def gone_then_404(contact_id):
        c.contacts.pop(contact_id, None)
        raise GHLError(404, "not found")

    monkeypatch.setattr(c, "delete_contact", gone_then_404)
    result = cleanup_test_data(c, _ledger([cid]))
    assert result.clean is True


def test_delete_contact_500_propagates(monkeypatch):
    c = FakeClient()
    cid = _contact(c, PHONE, tags=["bot-test"])

    def boom(contact_id):
        raise GHLError(500, "server")

    monkeypatch.setattr(c, "delete_contact", boom)
    with pytest.raises(GHLError) as exc:
        cleanup_test_data(c, _ledger([cid]))
    assert exc.value.status == 500


def test_deleted_seed_is_removed_from_ledger_seeds():
    c = FakeClient()
    cid = _contact(c, SEEDS["client"]["phone"], tags=["bot-test"])
    ledger = _ledger([cid])
    ledger["seeds"] = {"client": cid}
    cleanup_test_data(c, ledger)
    assert ledger["seeds"] == {}


def test_kept_seed_stays_in_ledger_seeds():
    c = FakeClient()
    cid = _contact(c, SEEDS["client"]["phone"])  # no bot-test tag: protected
    ledger = _ledger([cid])
    ledger["seeds"] = {"client": cid}
    cleanup_test_data(c, ledger)
    assert ledger["seeds"] == {"client": cid} and cid in c.contacts


def test_collisions_clean_account_passes():
    assert find_collisions(FakeClient(), _ledger()) == []


def test_collision_on_test_phone_outside_ledger_is_reported():
    c = FakeClient()
    cid = _contact(c, PHONE)
    found = find_collisions(c, _ledger())
    assert found == [(PHONE, cid)]


def test_collision_on_test_email_outside_ledger_is_reported():
    c = FakeClient()
    email = TEST_LEADS[0]["email"]
    cid = _contact(c, "+15125550999", email=email)
    assert find_collisions(c, _ledger()) == [(email, cid)]


def test_contact_already_in_ledger_is_not_a_collision():
    c = FakeClient()
    cid = _contact(c, PHONE, tags=["bot-test"], email=TEST_LEADS[0]["email"])
    assert find_collisions(c, _ledger([cid])) == []
