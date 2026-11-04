import json

import pytest

from leadbot.ghl.client import GHLAuthError, GHLClient, GHLError

TOKEN = "pit-secret-123"


class Recorder:
    """Fake transport: returns queued responses and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, json.loads(body) if body else None))
        status, payload, hdrs = self.responses.pop(0)
        return status, hdrs, json.dumps(payload).encode() if payload is not None else b""


def make(*responses):
    rec = Recorder(*responses)
    sleeps = []
    client = GHLClient(TOKEN, "LOC1", transport=rec, sleep=sleeps.append, max_retries=3)
    return client, rec, sleeps


def test_sends_auth_version_and_json():
    from leadbot.ghl.client import USER_AGENT

    client, rec, _ = make((201, {"tag": {"id": "t1", "name": "bot-lead"}}, {}))
    assert client.create_tag("bot-lead") == {"id": "t1", "name": "bot-lead"}
    method, url, headers, body = rec.calls[0]
    assert method == "POST"
    assert url == "https://services.leadconnectorhq.com/locations/LOC1/tags"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Version"] == "2021-07-28"
    assert headers["User-Agent"] == USER_AGENT
    assert not headers["User-Agent"].startswith("Python-urllib")
    assert body == {"name": "bot-lead"}


def test_retries_429_using_retry_after_then_succeeds():
    client, rec, sleeps = make(
        (429, {"message": "slow down"}, {"Retry-After": "3"}),
        (200, {"tags": []}, {}),
    )
    assert client.list_tags() == []
    assert sleeps == [3.0]
    assert len(rec.calls) == 2


def test_retries_500_with_exponential_backoff_and_gives_up():
    client, _, sleeps = make(*[(502, {"message": "bad gateway"}, {})] * 4)
    with pytest.raises(GHLError) as exc:
        client.list_tags()
    assert exc.value.status == 502
    assert sleeps == [1, 2, 4]


def test_auth_error_names_scopes_and_hides_token():
    client, _, _ = make((401, {"message": "Invalid JWT"}, {}))
    with pytest.raises(GHLAuthError) as exc:
        client.list_tags()
    assert "scopes" in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_cloudflare_1010_403_names_user_agent_and_hides_token():
    client, _, _ = make((403, {"title": "Error 1010: Access denied", "status": 403}, {}))
    with pytest.raises(GHLAuthError) as exc:
        client.list_tags()
    assert exc.value.status == 403
    assert exc.value.message == (
        "Cloudflare blocked the request (error 1010); check the User-Agent header")
    assert "scopes" not in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_422_raises_with_ghl_message_list_joined():
    client, _, _ = make((422, {"message": ["phone must be valid", "name too long"]}, {}))
    with pytest.raises(GHLError, match="phone must be valid; name too long"):
        client.upsert_contact({"locationId": "LOC1"})


def test_upsert_returns_contact_and_new_flag():
    client, rec, _ = make((200, {"new": True, "contact": {"id": "c1"}, "traceId": "x"}, {}))
    contact, is_new = client.upsert_contact({"locationId": "LOC1", "phone": "+15125550101"})
    assert (contact, is_new) == ({"id": "c1"}, True)
    assert rec.calls[0][1].endswith("/contacts/upsert")


def test_find_duplicate_encodes_phone_and_returns_contact_or_none():
    client, rec, _ = make((200, {"contact": {"id": "c9"}}, {}), (200, {"contact": None}, {}))
    assert client.find_duplicate("+15125550101", None) == {"id": "c9"}
    assert "number=%2B15125550101" in rec.calls[0][1]
    assert "email" not in rec.calls[0][1]
    assert client.find_duplicate(None, "a@example.com") is None


def test_search_opportunities_uses_snake_case_query():
    client, rec, _ = make((200, {"opportunities": [{"id": "o1"}], "meta": {}}, {}))
    assert client.search_opportunities("c1", pipeline_id="P1", status="lost") == [{"id": "o1"}]
    url = rec.calls[0][1]
    for part in ["location_id=LOC1", "contact_id=c1", "pipeline_id=P1", "status=lost", "limit=100"]:
        assert part in url


def test_get_contact_returns_none_on_404():
    client, _, _ = make((404, {"message": "not found"}, {}))
    assert client.get_contact("gone") is None


def test_get_opportunity_returns_none_on_404():
    client, _, _ = make((404, {"message": "not found"}, {}))
    assert client.get_opportunity("gone") is None


def test_get_opportunity_unwraps_body():
    client, rec, _ = make((200, {"opportunity": {"id": "O1", "status": "open"}}, {}))
    assert client.get_opportunity("O1") == {"id": "O1", "status": "open"}
    assert rec.calls[0][1].endswith("/opportunities/O1")


def test_create_pipeline_positions_stages():
    client, rec, _ = make((200, {"id": "P1", "name": "Bot"}, {}))
    client.create_pipeline("Bot", ["New", "Won"])
    body = rec.calls[0][3]
    assert body["locationId"] == "LOC1"
    assert body["stages"] == [{"name": "New", "position": 0}, {"name": "Won", "position": 1}]


def test_create_custom_field_is_contact_model():
    client, rec, _ = make((201, {"customField": {"id": "f1"}}, {}))
    assert client.create_custom_field("Lead Score", "NUMERICAL") == {"id": "f1"}
    assert rec.calls[0][3] == {"name": "Lead Score", "dataType": "NUMERICAL", "model": "contact"}
