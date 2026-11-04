# RiseRidge Lead Bot Part A (GoHighLevel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the RiseRidge GHL sub-account so scored bot leads can be pushed in by script and worked by reps, proven by a 20-lead live test that is cleaned up afterwards.

**Architecture:** A stdlib-only Python package (`leadbot.ghl`) holds a small API v2 client, an idempotent setup routine driven by `config/ghl_schema.toml`, and `push_lead()`, which part B will call. Option-type custom fields and both workflows are built by hand in the GHL web app (Claude's built-in browser), because the API has no documented way to pass dropdown options or create workflows. A verify mode in the setup script checks the hand-built parts.

**Tech Stack:** Python 3.14 (stdlib `urllib`, `json`, `tomllib`, `dataclasses`), pytest 9.1.1, GHL API v2 at `https://services.leadconnectorhq.com` with `Version: 2021-07-28`.

**Spec:** `docs/superpowers/specs/2026-11-04-ghl-leadbot-design.md`

## Global Constraints

- Runtime dependencies: stdlib only. Test dependency: `pytest==9.1.1` (same pin as `riseridge-sales`).
- API header: `Version: 2021-07-28` on every request. Opportunity search uses that version's snake_case query names (`location_id`, `contact_id`, `pipeline_id`).
- The token is never printed, logged, or included in an exception message.
- `POST /contacts/upsert` is never sent a `tags` key (it overwrites all tags). Tags are added only with `POST /contacts/{id}/tags`.
- The bot never writes the Call Outcome field, and never sends a custom field whose payload value is missing or empty.
- Isolation in the live sub-account: bot tags start with `bot-`, `tier-`, `sig-` or `niche-`; the pipeline is "Bot Leads – SEO"; workflow names start with `[Lead Bot]`; test contacts carry `bot-test`, use phone numbers +1 512-555-01NN and `@example.com` emails.
- Setup never deletes or retypes anything in GHL; it reports drift.
- Market: US only. Phone numbers in E.164 (`+1...`), country `US`.
- Browser tasks: the user signs in to GHL; Claude never types passwords or the token into any page. The user puts the token into `.env` themselves.

## File Structure

```
riseridge-leadbot/
  .gitignore                    (exists) add state/
  .env                          user-created: GHL_TOKEN, GHL_LOCATION_ID (git-ignored)
  .env.example                  key names only
  requirements.txt              pytest pin
  conftest.py                   puts repo root on sys.path
  config/ghl_schema.toml        fields, options, tags, pipeline, signal mapping, duplicate rules
  leadbot/__init__.py
  leadbot/env.py                .env loading, client_from_env()
  leadbot/ghl/__init__.py
  leadbot/ghl/schema.py         load_schema() -> Schema; slug/tag helpers
  leadbot/ghl/client.py         GHLClient: HTTP, retries, endpoint methods
  leadbot/ghl/setup.py          run_setup() -> SetupReport
  leadbot/ghl/mapping.py        payload validation and GHL body building
  leadbot/ghl/push.py           load_context(), push_lead() -> PushResult
  leadbot/ghl/testdata.py       20 test leads + 2 seed contacts
  scripts/probe_ghl.py          read-only live probe of response shapes
  scripts/setup_ghl.py          CLI for run_setup (--dry-run / --verify)
  scripts/push_test_leads.py    live 20-lead run with expected-count check
  scripts/cleanup_test_leads.py deletes everything in the test ledger
  docs/ghl-build-sheet.md       UI-built parts, recorded as built
  tests/fakes.py                FakeClient (in-memory GHL) for unit tests
  tests/test_schema.py
  tests/test_client.py
  tests/test_setup.py
  tests/test_mapping.py
  tests/test_push.py
  tests/test_testdata.py
  state/test_ledger.json        written by the test scripts (git-ignored)
```

Spec deviation, deliberate: the spec named `ghl_schema.yaml`. This plan uses TOML because `tomllib` is stdlib and the project keeps zero runtime dependencies.

---

### Task 1: Project scaffold and schema config

**Files:**
- Create: `requirements.txt`, `conftest.py`, `.env.example`, `leadbot/__init__.py`, `leadbot/ghl/__init__.py`, `config/ghl_schema.toml`, `leadbot/ghl/schema.py`
- Modify: `.gitignore` (add `state/`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `leadbot.ghl.schema.OPTION_TYPES: frozenset[str]`
  - `FieldSpec(name: str, data_type: str, source: str, options: tuple[str, ...])` with property `created_by -> "api" | "ui"`
  - `Schema` (frozen dataclass): `pipeline_name: str`, `stages: tuple[str, ...]`, `entry_stage: str`, `client_tags: tuple[str, ...]`, `lost_window_days: int`, `static_tags: tuple[str, ...]`, `fields: tuple[FieldSpec, ...]`, `signals: dict[str, str]`, `signal_tags: dict[str, str]`; properties `tiers`, `niches`, `categories`; methods `field(name) -> FieldSpec`, `all_tags() -> list[str]`
  - `slug(text: str) -> str`, `tier_tag(tier: str) -> str`, `niche_tag(niche: str) -> str`
  - `load_schema(path: str | os.PathLike = DEFAULT_SCHEMA_PATH) -> Schema`; raises `SchemaError(ValueError)`
  - Constants `TIER_FIELD = "Lead Tier"`, `NICHE_FIELD = "Niche"`, `PRIMARY_FIELD = "Primary Signal"`, `ALL_SIGNALS_FIELD = "All Signals"`

- [ ] **Step 1: Scaffold files**

`requirements.txt`:
```
# Test dependency only. The package itself is stdlib.
pytest==9.1.1
```

`conftest.py`:
```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```

`.env.example`:
```
# Copy to .env and fill in. Never commit .env.
GHL_TOKEN=
GHL_LOCATION_ID=
```

`leadbot/__init__.py` and `leadbot/ghl/__init__.py`: empty files.

Append to `.gitignore`:
```
state/
```

Run: `python -m pip install -r requirements.txt`
Expected: pytest 9.1.1 installed or already satisfied.

- [ ] **Step 2: Write `config/ghl_schema.toml`**

```toml
# Single source of truth for everything the bot creates or writes in GHL.
# Option-type fields (data_type SINGLE_OPTIONS, MULTIPLE_OPTIONS, CHECKBOX)
# are created by hand in the GHL UI; setup_ghl.py --verify checks them.

[pipeline]
name = "Bot Leads – SEO"
stages = ["New", "Contacted", "Conversation", "Audit Sent", "Proposal", "Won", "Lost", "Nurture"]
entry_stage = "New"

[duplicate]
# Tags that mark an existing client in this sub-account. Filled in Task 6
# from the browser check. Empty means only the lost-deal rule applies.
client_tags = []
lost_window_days = 90

[tags]
static = ["bot-lead", "bot-dupe", "bot-test", "bot-nurture-90", "bot-try-1", "bot-try-2", "bot-try-3"]

# Bot signal flag -> script category (spec 4.2).
[signals]
new_business = "New Biz"
gbp_unverified_or_suspended = "GBP Suspended"
broken_website = "Broken Site"
google_ads_spend = "Ad Waste"
meta_ads_active = "Ad Waste"
rank_gap = "Rank Gap"
technical_debt = "Rank Gap"
competitor_gap = "Rank Gap"

[signal_tags]
"New Biz" = "sig-new"
"GBP Suspended" = "sig-gbp"
"Broken Site" = "sig-site"
"Ad Waste" = "sig-ads"
"Rank Gap" = "sig-rank"

# source = payload key the value comes from; "" = never written by the bot.
[[fields]]
name = "Lead Score"
data_type = "NUMERICAL"
source = "lead_score"

[[fields]]
name = "Lead Tier"
data_type = "SINGLE_OPTIONS"
source = "lead_tier"
options = ["Hot", "Warm", "Cold"]

[[fields]]
name = "Niche"
data_type = "SINGLE_OPTIONS"
source = "niche"
options = ["Counselors / therapists", "Roofing", "HVAC", "Electricians", "Personal injury lawyers", "Med spas / dental"]

[[fields]]
name = "Target City"
data_type = "TEXT"
source = "city"

[[fields]]
name = "Primary Signal"
data_type = "SINGLE_OPTIONS"
source = "primary_signal"
options = ["New Biz", "GBP Suspended", "Broken Site", "Ad Waste", "Rank Gap"]

[[fields]]
name = "All Signals"
data_type = "MULTIPLE_OPTIONS"
source = "signals"
options = ["New Biz", "GBP Suspended", "Broken Site", "Ad Waste", "Rank Gap"]

[[fields]]
name = "GBP URL"
data_type = "TEXT"
source = "gbp_url"

[[fields]]
name = "GBP Status"
data_type = "SINGLE_OPTIONS"
source = "gbp_status"
options = ["Verified", "Unverified", "Suspended suspected"]

[[fields]]
name = "Review Count"
data_type = "NUMERICAL"
source = "review_count"

[[fields]]
name = "Rating"
data_type = "FLOAT"
source = "rating"

[[fields]]
name = "Est. Google Ads Spend"
data_type = "MONETORY"
source = "est_google_ads_spend"

[[fields]]
name = "Meta Ads Active"
data_type = "CHECKBOX"
source = "meta_ads_active"
options = ["Yes"]

[[fields]]
name = "Money Keyword"
data_type = "TEXT"
source = "money_keyword"

[[fields]]
name = "Current Rank"
data_type = "NUMERICAL"
source = "current_rank"

[[fields]]
name = "Audit Summary"
data_type = "LARGE_TEXT"
source = "audit_summary"

[[fields]]
name = "Call Opener"
data_type = "LARGE_TEXT"
source = "call_opener"

[[fields]]
name = "Bot Run ID"
data_type = "TEXT"
source = "bot_run_id"

[[fields]]
name = "Call Outcome"
data_type = "SINGLE_OPTIONS"
source = ""
options = ["No answer", "Callback", "Interested", "Not interested", "Closed"]
```

(`MONETORY` is GHL's own spelling of the dataType enum.)

- [ ] **Step 3: Write the failing tests**

`tests/test_schema.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'leadbot.ghl.schema'`

- [ ] **Step 5: Write `leadbot/ghl/schema.py`**

```python
"""Loads config/ghl_schema.toml: the one place GHL names, options and tags live."""

import os
import re
import tomllib
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SCHEMA_PATH = os.path.join(ROOT, "config", "ghl_schema.toml")

OPTION_TYPES = frozenset({"SINGLE_OPTIONS", "MULTIPLE_OPTIONS", "CHECKBOX", "RADIO"})

TIER_FIELD = "Lead Tier"
NICHE_FIELD = "Niche"
PRIMARY_FIELD = "Primary Signal"
ALL_SIGNALS_FIELD = "All Signals"


class SchemaError(ValueError):
    pass


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tier_tag(tier):
    return "tier-" + slug(tier)


def niche_tag(niche):
    return "niche-" + slug(niche)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    data_type: str
    source: str
    options: tuple = ()

    @property
    def created_by(self):
        return "ui" if self.data_type in OPTION_TYPES else "api"


@dataclass(frozen=True)
class Schema:
    pipeline_name: str
    stages: tuple
    entry_stage: str
    client_tags: tuple
    lost_window_days: int
    static_tags: tuple
    fields: tuple
    signals: dict
    signal_tags: dict

    def field(self, name):
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    @property
    def tiers(self):
        return self.field(TIER_FIELD).options

    @property
    def niches(self):
        return self.field(NICHE_FIELD).options

    @property
    def categories(self):
        return tuple(self.signal_tags)

    def all_tags(self):
        tags = list(self.static_tags)
        tags += [tier_tag(t) for t in self.tiers]
        tags += [niche_tag(n) for n in self.niches]
        tags += list(self.signal_tags.values())
        seen, out = set(), []
        for t in tags:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out


def load_schema(path=DEFAULT_SCHEMA_PATH):
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    p = raw["pipeline"]
    fields = tuple(
        FieldSpec(f["name"], f["data_type"], f.get("source", ""), tuple(f.get("options", ())))
        for f in raw.get("fields", [])
    )
    schema = Schema(
        pipeline_name=p["name"],
        stages=tuple(p["stages"]),
        entry_stage=p["entry_stage"],
        client_tags=tuple(raw["duplicate"]["client_tags"]),
        lost_window_days=int(raw["duplicate"]["lost_window_days"]),
        static_tags=tuple(raw["tags"]["static"]),
        fields=fields,
        signals=dict(raw["signals"]),
        signal_tags=dict(raw["signal_tags"]),
    )
    _validate(schema)
    return schema


def _validate(schema):
    if schema.entry_stage not in schema.stages:
        raise SchemaError(f"entry_stage {schema.entry_stage!r} is not one of the pipeline stages")
    if not schema.fields:
        return
    for category in schema.signals.values():
        if category not in schema.signal_tags:
            raise SchemaError(f"signal category {category!r} has no entry in [signal_tags]")
    for name in (PRIMARY_FIELD, ALL_SIGNALS_FIELD):
        if set(schema.field(name).options) != set(schema.signal_tags):
            raise SchemaError(f"{name} options must equal the [signal_tags] categories")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_schema.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add requirements.txt conftest.py .env.example .gitignore leadbot config tests/test_schema.py
git commit -m "feat: GHL schema config and loader"
```

---

### Task 2: GHL API client

**Files:**
- Create: `leadbot/ghl/client.py`, `leadbot/env.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Produces:
  - `GHLError(Exception)` with `.status: int`, `.message: str`; subclass `GHLAuthError`
  - `GHLClient(token: str, location_id: str, *, base_url=BASE_URL, transport=urllib_transport, sleep=time.sleep, max_retries=5)`
  - Transport signature: `transport(method: str, url: str, headers: dict, body: bytes | None) -> (status: int, headers: dict, body: bytes)`
  - `GHLClient.request(method, path, *, query=None, body=None) -> dict`
  - Endpoint methods (all return parsed JSON pieces):
    - `list_custom_fields() -> list[dict]`
    - `create_custom_field(name: str, data_type: str) -> dict`
    - `list_tags() -> list[dict]`
    - `create_tag(name: str) -> dict`
    - `list_pipelines() -> list[dict]`
    - `create_pipeline(name: str, stages: list[str]) -> dict`
    - `find_duplicate(phone: str | None, email: str | None) -> dict | None`
    - `upsert_contact(body: dict) -> tuple[dict, bool]` (contact, is_new)
    - `add_tags(contact_id: str, tags: list[str]) -> list[str]`
    - `get_contact(contact_id: str) -> dict | None`
    - `delete_contact(contact_id: str) -> None`
    - `search_opportunities(contact_id: str, *, pipeline_id: str | None = None, status: str | None = None) -> list[dict]`
    - `create_opportunity(body: dict) -> dict`
    - `delete_opportunity(opportunity_id: str) -> None`
  - `leadbot.env.client_from_env(root: str = ROOT) -> GHLClient`

- [ ] **Step 1: Write the failing tests**

`tests/test_client.py`:
```python
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
    client, rec, _ = make((201, {"tag": {"id": "t1", "name": "bot-lead"}}, {}))
    assert client.create_tag("bot-lead") == {"id": "t1", "name": "bot-lead"}
    method, url, headers, body = rec.calls[0]
    assert method == "POST"
    assert url == "https://services.leadconnectorhq.com/locations/LOC1/tags"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Version"] == "2021-07-28"
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'leadbot.ghl.client'`

- [ ] **Step 3: Write `leadbot/ghl/client.py`**

```python
"""Minimal GoHighLevel API v2 client. Stdlib only; the token never leaves the headers."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"
SCOPE_HINT = ("Check the Private Integration token scopes: contacts, opportunities, "
              "custom fields and tags (read and write), and pipelines.")


class GHLError(Exception):
    def __init__(self, status, message):
        super().__init__(f"GHL {status}: {message}")
        self.status = status
        self.message = message


class GHLAuthError(GHLError):
    pass


def urllib_transport(method, url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _decode(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {"message": raw.decode("utf-8", "replace")[:200]}


def _message(payload):
    msg = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(msg, list):
        return "; ".join(str(m) for m in msg)
    if msg:
        return str(msg)
    return str(payload)[:200]


def _retry_delay(headers, attempt):
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except ValueError:
                break
    return 2 ** attempt


class GHLClient:
    def __init__(self, token, location_id, *, base_url=BASE_URL,
                 transport=urllib_transport, sleep=time.sleep, max_retries=5):
        self._token = token
        self.location_id = location_id
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep
        self.max_retries = max_retries

    def request(self, method, path, *, query=None, body=None):
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Version": API_VERSION,
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in range(self.max_retries + 1):
            status, resp_headers, raw = self._transport(method, url, headers, data)
            if status == 429 or status >= 500:
                if attempt == self.max_retries:
                    raise GHLError(status, f"gave up after {self.max_retries} retries")
                self._sleep(_retry_delay(resp_headers, attempt))
                continue
            payload = _decode(raw)
            if status in (401, 403):
                raise GHLAuthError(status, f"{_message(payload)}. {SCOPE_HINT}")
            if status >= 400:
                raise GHLError(status, _message(payload))
            return payload
        raise GHLError(0, "unreachable")

    # Custom fields, tags, pipelines -----------------------------------------
    def list_custom_fields(self):
        data = self.request("GET", f"/locations/{self.location_id}/customFields",
                            query={"model": "contact"})
        return data.get("customFields", [])

    def create_custom_field(self, name, data_type):
        data = self.request("POST", f"/locations/{self.location_id}/customFields",
                            body={"name": name, "dataType": data_type, "model": "contact"})
        return data.get("customField", data)

    def list_tags(self):
        return self.request("GET", f"/locations/{self.location_id}/tags").get("tags", [])

    def create_tag(self, name):
        data = self.request("POST", f"/locations/{self.location_id}/tags", body={"name": name})
        return data.get("tag", data)

    def list_pipelines(self):
        data = self.request("GET", "/opportunities/pipelines",
                            query={"locationId": self.location_id})
        return data.get("pipelines", [])

    def create_pipeline(self, name, stages):
        body = {
            "name": name,
            "locationId": self.location_id,
            "stages": [{"name": s, "position": i} for i, s in enumerate(stages)],
        }
        return self.request("POST", "/opportunities/pipelines", body=body)

    # Contacts ----------------------------------------------------------------
    def find_duplicate(self, phone, email):
        data = self.request("GET", "/contacts/search/duplicate", query={
            "locationId": self.location_id, "number": phone, "email": email})
        return data.get("contact") or None

    def upsert_contact(self, body):
        data = self.request("POST", "/contacts/upsert", body=body)
        return data["contact"], bool(data.get("new"))

    def add_tags(self, contact_id, tags):
        data = self.request("POST", f"/contacts/{contact_id}/tags", body={"tags": list(tags)})
        return data.get("tags", [])

    def get_contact(self, contact_id):
        try:
            return self.request("GET", f"/contacts/{contact_id}").get("contact")
        except GHLError as exc:
            if exc.status in (400, 404):
                return None
            raise

    def delete_contact(self, contact_id):
        self.request("DELETE", f"/contacts/{contact_id}")

    # Opportunities -----------------------------------------------------------
    def search_opportunities(self, contact_id, *, pipeline_id=None, status=None):
        data = self.request("GET", "/opportunities/search", query={
            "location_id": self.location_id, "contact_id": contact_id,
            "pipeline_id": pipeline_id, "status": status, "limit": 100})
        return data.get("opportunities", [])

    def create_opportunity(self, body):
        data = self.request("POST", "/opportunities/", body=body)
        return data.get("opportunity", data)

    def delete_opportunity(self, opportunity_id):
        self.request("DELETE", f"/opportunities/{opportunity_id}")
```

- [ ] **Step 4: Write `leadbot/env.py`**

```python
"""Reads .env into os.environ (real environment wins) and builds the client."""

import os

from leadbot.ghl.client import GHLClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def client_from_env(root=ROOT):
    load_dotenv(os.path.join(root, ".env"))
    missing = [k for k in ("GHL_TOKEN", "GHL_LOCATION_ID") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}")
    return GHLClient(os.environ["GHL_TOKEN"], os.environ["GHL_LOCATION_ID"])
```

(`utf-8-sig` strips a BOM if the user saved `.env` from Windows PowerShell 5.1.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_client.py -v`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add leadbot/ghl/client.py leadbot/env.py tests/test_client.py
git commit -m "feat: GHL API v2 client with retries"
```

---

### Task 3: Token and live read-only probe (user + Claude)

This task needs the user. It confirms the API version, the token scopes, and three response shapes the code assumes, before anything is written to GHL.

**Files:**
- Create: `scripts/probe_ghl.py`
- Test: none (live, read-only)

**Interfaces:**
- Consumes: `leadbot.env.client_from_env()`, `GHLClient.request`, `list_pipelines`, `list_custom_fields`, `list_tags`, `find_duplicate`

- [ ] **Step 1: Write `scripts/probe_ghl.py`**

```python
"""Read-only probe: prints response SHAPES (keys and counts), never values."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leadbot.env import client_from_env  # noqa: E402


def keys(obj):
    return sorted(obj) if isinstance(obj, dict) else type(obj).__name__


def main():
    c = client_from_env()
    pipelines = c.list_pipelines()
    print(f"pipelines: {len(pipelines)}; pipeline keys: {keys(pipelines[0]) if pipelines else '-'}")
    if pipelines and pipelines[0].get("stages"):
        print(f"stage keys: {keys(pipelines[0]['stages'][0])}")
    fields = c.list_custom_fields()
    print(f"custom fields: {len(fields)}; field keys: {keys(fields[0]) if fields else '-'}")
    tags = c.list_tags()
    print(f"tags: {len(tags)}; tag keys: {keys(tags[0]) if tags else '-'}")
    raw = c.request("GET", "/contacts/search/duplicate",
                    query={"locationId": c.location_id, "number": "+15125550199"})
    print(f"duplicate search (no match expected) top-level keys: {keys(raw)}")
    print("PROBE OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: User creates the token**

In the RiseRidge sub-account: Settings → Private Integrations → Create. Tick these scopes (exact labels as shown in the GHL list; check each one is present):
- contacts.readonly, contacts.write
- opportunities.readonly, opportunities.write
- locations.readonly
- locations/customFields.readonly, locations/customFields.write
- locations/tags.readonly, locations/tags.write
- any pipelines scope offered (the docs name `pipelines.create`)

The user copies `.env.example` to `.env` and fills in `GHL_TOKEN` and `GHL_LOCATION_ID` (Settings → Business Profile shows the Location ID). Claude does not handle the token.

- [ ] **Step 3: Run the probe**

Run: `python scripts/probe_ghl.py`
Expected: lines for pipelines, custom fields, tags, duplicate search, then `PROBE OK`.

Check against the code's assumptions:
- Stage keys include `id` and `name` (used by `load_context` in Task 7).
- Field keys include `id`, `name`, `dataType` and `picklistOptions` (used by setup verify).
- Tag keys include `name`.
- Duplicate search keys include `contact` (`find_duplicate` reads `data.get("contact")`).

If a 401/403 appears, the user adds the named scope and re-runs. If an assumed key is missing, stop and fix the reading code and its unit test before Task 4. If the API rejects `Version: 2021-07-28`, stop: the opportunity-search query names depend on it and this plan needs revising.

- [ ] **Step 4: Commit**

```bash
git add scripts/probe_ghl.py
git commit -m "feat: read-only GHL probe script"
```

---

### Task 4: Idempotent setup (tags, API fields, pipeline, verify)

**Files:**
- Create: `leadbot/ghl/setup.py`, `scripts/setup_ghl.py`, `tests/fakes.py`
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `Schema`, `FieldSpec.created_by`, client methods `list_tags`, `create_tag`, `list_custom_fields`, `create_custom_field`, `list_pipelines`, `create_pipeline`
- Produces:
  - `SetupReport` dataclass: `created: list[str]`, `existing: list[str]`, `drift: list[str]`, `missing_ui: list[str]`; property `ok -> bool` (no drift and nothing missing)
  - `run_setup(client, schema, *, dry_run: bool = False, verify_only: bool = False) -> SetupReport`
  - `tests.fakes.FakeClient(location_id="LOC1")` implementing every `GHLClient` endpoint method in memory, plus attributes `contacts: dict[str, dict]`, `opportunities: dict[str, dict]`, `fields: list[dict]`, `tags: list[dict]`, `pipelines: list[dict]`, `calls: list[tuple]`

- [ ] **Step 1: Write `tests/fakes.py`**

```python
"""In-memory stand-in for GHLClient. Same method names and return shapes."""

import itertools


class FakeClient:
    def __init__(self, location_id="LOC1"):
        self.location_id = location_id
        self.fields, self.tags, self.pipelines = [], [], []
        self.contacts, self.opportunities = {}, {}
        self.calls = []
        self._ids = itertools.count(1)

    def _id(self, prefix):
        return f"{prefix}{next(self._ids)}"

    # setup
    def list_custom_fields(self):
        return list(self.fields)

    def create_custom_field(self, name, data_type):
        self.calls.append(("create_custom_field", name, data_type))
        f = {"id": self._id("f"), "name": name, "dataType": data_type, "picklistOptions": []}
        self.fields.append(f)
        return f

    def list_tags(self):
        return list(self.tags)

    def create_tag(self, name):
        self.calls.append(("create_tag", name))
        t = {"id": self._id("t"), "name": name}
        self.tags.append(t)
        return t

    def list_pipelines(self):
        return list(self.pipelines)

    def create_pipeline(self, name, stages):
        self.calls.append(("create_pipeline", name, tuple(stages)))
        p = {"id": self._id("p"), "name": name,
             "stages": [{"id": self._id("s"), "name": s} for s in stages]}
        self.pipelines.append(p)
        return p

    # contacts
    def find_duplicate(self, phone, email):
        for c in self.contacts.values():
            if (phone and c.get("phone") == phone) or (email and c.get("email") == email):
                return dict(c)
        return None

    def upsert_contact(self, body):
        self.calls.append(("upsert_contact", body))
        existing = self.find_duplicate(body.get("phone"), body.get("email"))
        if existing:
            c = self.contacts[existing["id"]]
            c.update({k: v for k, v in body.items() if k != "customFields"})
            c.setdefault("customFields", {}).update(
                {cf["id"]: cf["fieldValue"] for cf in body.get("customFields", [])})
            return dict(c), False
        cid = self._id("c")
        c = {k: v for k, v in body.items() if k != "customFields"}
        c.update(id=cid, tags=[],
                 customFields={cf["id"]: cf["fieldValue"] for cf in body.get("customFields", [])})
        self.contacts[cid] = c
        return dict(c), True

    def add_tags(self, contact_id, tags):
        self.calls.append(("add_tags", contact_id, tuple(tags)))
        c = self.contacts[contact_id]
        for t in tags:
            if t not in c["tags"]:
                c["tags"].append(t)
        return list(c["tags"])

    def get_contact(self, contact_id):
        c = self.contacts.get(contact_id)
        return dict(c) if c else None

    def delete_contact(self, contact_id):
        self.calls.append(("delete_contact", contact_id))
        self.contacts.pop(contact_id, None)

    # opportunities
    def search_opportunities(self, contact_id, *, pipeline_id=None, status=None):
        out = []
        for o in self.opportunities.values():
            if o["contactId"] != contact_id:
                continue
            if pipeline_id and o["pipelineId"] != pipeline_id:
                continue
            if status and status != "all" and o["status"] != status:
                continue
            out.append(dict(o))
        return out

    def create_opportunity(self, body):
        self.calls.append(("create_opportunity", body))
        oid = self._id("o")
        o = dict(body, id=oid)
        o.setdefault("lastStatusChangeAt", "2026-11-04T09:00:00.000Z")
        self.opportunities[oid] = o
        return dict(o)

    def delete_opportunity(self, opportunity_id):
        self.calls.append(("delete_opportunity", opportunity_id))
        self.opportunities.pop(opportunity_id, None)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_setup.py`:
```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'leadbot.ghl.setup'`

- [ ] **Step 4: Write `leadbot/ghl/setup.py`**

```python
"""Creates what is missing, verifies the rest. Never deletes or retypes."""

from dataclasses import dataclass, field


@dataclass
class SetupReport:
    created: list = field(default_factory=list)
    existing: list = field(default_factory=list)
    drift: list = field(default_factory=list)
    missing_ui: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.drift and not self.missing_ui


def run_setup(client, schema, *, dry_run=False, verify_only=False):
    report = SetupReport()
    write = not (dry_run or verify_only)

    have_tags = {t["name"].lower() for t in client.list_tags()}
    for tag in schema.all_tags():
        if tag.lower() in have_tags:
            report.existing.append(f"tag {tag}")
        elif verify_only:
            report.drift.append(f"tag {tag} is missing")
        else:
            if write:
                client.create_tag(tag)
            report.created.append(f"tag {tag}")

    have_fields = {f["name"]: f for f in client.list_custom_fields()}
    for spec in schema.fields:
        found = have_fields.get(spec.name)
        if found is None:
            if spec.created_by == "ui":
                report.missing_ui.append(spec.name)
            elif verify_only:
                report.drift.append(f"field {spec.name} is missing")
            else:
                if write:
                    client.create_custom_field(spec.name, spec.data_type)
                report.created.append(f"field {spec.name}")
            continue
        report.existing.append(f"field {spec.name}")
        if found.get("dataType") != spec.data_type:
            report.drift.append(
                f"field {spec.name} is {found.get('dataType')}, expected {spec.data_type}")
        missing = [o for o in spec.options if o not in (found.get("picklistOptions") or [])]
        if missing:
            report.drift.append(f"field {spec.name} lacks options: {', '.join(missing)}")

    pipeline = next((p for p in client.list_pipelines()
                     if p["name"].lower() == schema.pipeline_name.lower()), None)
    if pipeline is None:
        if verify_only:
            report.drift.append(f"pipeline {schema.pipeline_name} is missing")
        else:
            if write:
                client.create_pipeline(schema.pipeline_name, list(schema.stages))
            report.created.append(f"pipeline {schema.pipeline_name}")
    else:
        report.existing.append(f"pipeline {schema.pipeline_name}")
        names = tuple(s["name"] for s in pipeline.get("stages", []))
        if names != schema.stages:
            report.drift.append(
                f"pipeline {schema.pipeline_name} stages are {list(names)}, "
                f"expected {list(schema.stages)}")
    return report
```

- [ ] **Step 5: Write `scripts/setup_ghl.py`**

```python
"""Create missing GHL objects (default), preview (--dry-run) or check (--verify)."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leadbot.env import client_from_env  # noqa: E402
from leadbot.ghl.schema import load_schema  # noqa: E402
from leadbot.ghl.setup import run_setup  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    report = run_setup(client_from_env(), load_schema(),
                       dry_run=args.dry_run, verify_only=args.verify)
    label = "would create" if args.dry_run else "created"
    for line in report.created:
        print(f"{label}: {line}")
    print(f"already there: {len(report.existing)}")
    for name in report.missing_ui:
        print(f"build in the GHL UI: field {name}")
    for line in report.drift:
        print(f"DRIFT: {line}")
    print("OK" if report.ok else "NOT OK")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_setup.py -v`
Expected: 6 passed

- [ ] **Step 7: Live run against the sub-account**

Run: `python scripts/setup_ghl.py --dry-run`
Expected: `would create:` lines for 21 tags, 11 fields and the pipeline (minus anything that already exists), 7 `build in the GHL UI` lines, `NOT OK`. Show the output to the user and get a yes before the next command, since it writes to the live sub-account.

Run: `python scripts/setup_ghl.py`
Expected: the same items as `created:`, then `NOT OK` (only the 7 UI fields outstanding).

Run: `python scripts/setup_ghl.py`
Expected: no `created:` lines (idempotent), still the 7 UI-field lines.

- [ ] **Step 8: Commit**

```bash
git add leadbot/ghl/setup.py scripts/setup_ghl.py tests/fakes.py tests/test_setup.py
git commit -m "feat: idempotent GHL setup with verify mode"
```

---

### Task 5: Payload validation and GHL body mapping

**Files:**
- Create: `leadbot/ghl/mapping.py`
- Test: `tests/test_mapping.py`

**Interfaces:**
- Consumes: `Schema`, `tier_tag`, `niche_tag`, field-name constants from Task 1
- Produces:
  - `PayloadError(ValueError)`
  - `validate_payload(payload: dict, schema: Schema) -> None`
  - `signal_categories(payload: dict, schema: Schema) -> list[str]`
  - `primary_category(payload: dict, schema: Schema) -> str | None`
  - `field_values(payload: dict, schema: Schema) -> dict[str, object]` (field name -> value)
  - `contact_body(payload: dict, schema: Schema, field_ids: dict[str, str], location_id: str) -> dict`
  - `lead_tags(payload: dict, schema: Schema) -> list[str]`
  - `SAMPLE_PAYLOAD: dict` (the SOP Step 6 example, reused by later tests)

- [ ] **Step 1: Write the failing tests**

`tests/test_mapping.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mapping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'leadbot.ghl.mapping'`

- [ ] **Step 3: Write `leadbot/ghl/mapping.py`**

```python
"""Turns one bot payload (SOP Step 6 JSON) into GHL request bodies."""

from leadbot.ghl.schema import ALL_SIGNALS_FIELD, PRIMARY_FIELD, niche_tag, tier_tag

SAMPLE_PAYLOAD = {
    "business_name": "Lone Star Cooling",
    "phone": "+15125550142",
    "email": "info@lonestarcooling.com",
    "website": "https://lonestarcooling.com",
    "niche": "HVAC",
    "city": "Austin, TX",
    "gbp_url": "https://maps.google.com/?cid=1",
    "gbp_status": "Unverified",
    "review_count": 14,
    "rating": 4.2,
    "est_google_ads_spend": 4500,
    "meta_ads_active": True,
    "money_keyword": "ac repair austin",
    "current_rank": 23,
    "signals": ["gbp_unverified_or_suspended", "google_ads_spend", "broken_website"],
    "primary_signal": "GBP Suspended",
    "lead_score": 82,
    "lead_tier": "Hot",
    "audit_summary": "Listing shows Claim this business. Ranks #23 for ac repair austin while bidding on it. No conversion tag on site.",
    "call_opener": "Hi, I noticed Lone Star Cooling's Google listing isn't verified, so it's hiding you from calls you're already paying ads for.",
    "bot_run_id": "2026-11-03-hvac-austin-0142",
}


class PayloadError(ValueError):
    pass


def _empty(value):
    return value is None or value == "" or value == []


def signal_categories(payload, schema):
    out = []
    for flag in payload.get("signals") or []:
        if flag not in schema.signals:
            raise PayloadError(f"unknown signal {flag!r}")
        category = schema.signals[flag]
        if category not in out:
            out.append(category)
    return out


def primary_category(payload, schema):
    value = payload.get("primary_signal")
    if _empty(value):
        return None
    if value in schema.signal_tags:
        return value
    if value in schema.signals:
        return schema.signals[value]
    raise PayloadError(f"primary_signal {value!r} is neither a category nor a signal flag")


def validate_payload(payload, schema):
    if _empty(payload.get("business_name")):
        raise PayloadError("business_name is required")
    if _empty(payload.get("phone")) and _empty(payload.get("email")):
        raise PayloadError("phone or email is required")
    if payload.get("lead_tier") not in schema.tiers:
        raise PayloadError(f"lead_tier {payload.get('lead_tier')!r} is not one of {schema.tiers}")
    if payload.get("niche") not in schema.niches:
        raise PayloadError(f"niche {payload.get('niche')!r} is not one of {schema.niches}")
    if _empty(payload.get("bot_run_id")):
        raise PayloadError("bot_run_id is required")
    signal_categories(payload, schema)
    primary_category(payload, schema)


def field_values(payload, schema):
    values = {}
    for spec in schema.fields:
        if not spec.source:
            continue
        if spec.name == ALL_SIGNALS_FIELD:
            value = signal_categories(payload, schema)
        elif spec.name == PRIMARY_FIELD:
            value = primary_category(payload, schema)
        elif spec.data_type == "CHECKBOX":
            if spec.source not in payload:
                continue
            value = [spec.options[0]] if payload[spec.source] else []
            values[spec.name] = value
            continue
        else:
            value = payload.get(spec.source)
        if _empty(value):
            continue
        values[spec.name] = value
    return values


def _split_city(city):
    if not city:
        return None, None
    if "," in city:
        name, state = city.split(",", 1)
        return name.strip(), state.strip()
    return city.strip(), None


def contact_body(payload, schema, field_ids, location_id):
    city, state = _split_city(payload.get("city"))
    body = {
        "locationId": location_id,
        "name": payload["business_name"],
        "companyName": payload["business_name"],
        "phone": payload.get("phone"),
        "email": payload.get("email"),
        "website": payload.get("website"),
        "city": city,
        "state": state,
        "country": "US",
        "source": "Lead Bot",
        "customFields": [
            {"id": field_ids[name], "fieldValue": value}
            for name, value in field_values(payload, schema).items()
        ],
    }
    return {k: v for k, v in body.items() if not _empty(v) or k == "customFields"}


def lead_tags(payload, schema):
    tags = ["bot-lead", tier_tag(payload["lead_tier"]), niche_tag(payload["niche"])]
    tags += [schema.signal_tags[c] for c in signal_categories(payload, schema)]
    return tags
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mapping.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add leadbot/ghl/mapping.py tests/test_mapping.py
git commit -m "feat: bot payload validation and GHL body mapping"
```

---

### Task 6: Build the option fields in the GHL UI and record the client marker (browser)

Claude does this in the built-in browser while the user is signed in to the RiseRidge sub-account.

**Files:**
- Create: `docs/ghl-build-sheet.md`
- Modify: `config/ghl_schema.toml` (`[duplicate] client_tags`)

**Interfaces:**
- Consumes: `scripts/setup_ghl.py --verify` from Task 4

- [ ] **Step 1: Create the folder and move the API fields**

Settings → Custom Fields: create folder "Lead Bot". Move the 11 fields created in Task 4 into it.

- [ ] **Step 2: Create the 7 option fields in folder "Lead Bot"**

Exact names, types and options, copied from `config/ghl_schema.toml` (object: Contact):

| Field | GHL type | Options, in order |
| --- | --- | --- |
| Lead Tier | Dropdown (single) | Hot, Warm, Cold |
| Niche | Dropdown (single) | Counselors / therapists, Roofing, HVAC, Electricians, Personal injury lawyers, Med spas / dental |
| Primary Signal | Dropdown (single) | New Biz, GBP Suspended, Broken Site, Ad Waste, Rank Gap |
| All Signals | Dropdown (multiple) | New Biz, GBP Suspended, Broken Site, Ad Waste, Rank Gap |
| GBP Status | Dropdown (single) | Verified, Unverified, Suspended suspected |
| Meta Ads Active | Checkbox | Yes |
| Call Outcome | Dropdown (single) | No answer, Callback, Interested, Not interested, Closed |

- [ ] **Step 3: Verify**

Run: `python scripts/setup_ghl.py --verify`
Expected: no `build in the GHL UI` lines, no `DRIFT` lines, `OK`.
If a UI type shows as a different `dataType` (for example Dropdown (single) reported as `RADIO`), change that field's `data_type` in `config/ghl_schema.toml` to the reported value, re-run `python -m pytest`, and note it in the build sheet.

- [ ] **Step 4: Find what marks an existing client**

Look at 2-3 known active clients in Contacts and Opportunities. Record whether a tag (for example `client`) marks them.
- A tag: put it in `client_tags`, for example `client_tags = ["client"]`.
- A pipeline stage only, or nothing consistent: stop and tell the user. The duplicate rule in spec section 5 step 3 needs a new decision before Task 7.

- [ ] **Step 5: Write `docs/ghl-build-sheet.md`**

```markdown
# GHL build sheet (as built)

Sub-account: RiseRidge. Built: <date of this step>.

## Custom fields
Folder "Lead Bot": 18 fields. 11 created by `scripts/setup_ghl.py`, 7 option fields created by hand (table in the plan, Task 6).
Type adjustments versus the plan: <none, or each change>.

## Client marker
<the tag(s) put in client_tags, and which contacts were checked>
```

Fill the three bracketed values with what this step found; no bracket may remain.

- [ ] **Step 6: Commit**

```bash
git add config/ghl_schema.toml docs/ghl-build-sheet.md
git commit -m "chore: record UI-built fields and client marker"
```

---

### Task 7: push_lead with duplicate guard

**Files:**
- Create: `leadbot/ghl/push.py`
- Test: `tests/test_push.py`

**Interfaces:**
- Consumes: `validate_payload`, `contact_body`, `lead_tags`, `PayloadError` (Task 5); client methods (Task 2); `FakeClient` (Task 4); `SAMPLE_PAYLOAD`
- Produces:
  - `SetupIncomplete(RuntimeError)`
  - `Context` dataclass: `field_ids: dict[str, str]`, `pipeline_id: str`, `stage_ids: dict[str, str]`
  - `load_context(client, schema) -> Context`
  - `PushResult` dataclass: `outcome: str` (one of `created`, `updated`, `existing`, `duplicate`, `rejected`, `failed`), `contact_id: str | None`, `opportunity_id: str | None`, `message: str`
  - `push_lead(client, schema, ctx, payload, *, extra_tags=(), today: datetime.date | None = None) -> PushResult`

- [ ] **Step 1: Write the failing tests**

`tests/test_push.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_push.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'leadbot.ghl.push'`

- [ ] **Step 3: Write `leadbot/ghl/push.py`**

```python
"""push_lead(): the one entry point the bot (part B) calls."""

import datetime as dt
from dataclasses import dataclass

from leadbot.ghl.client import GHLAuthError, GHLError
from leadbot.ghl.mapping import PayloadError, contact_body, lead_tags, validate_payload


class SetupIncomplete(RuntimeError):
    pass


@dataclass
class Context:
    field_ids: dict
    pipeline_id: str
    stage_ids: dict


@dataclass
class PushResult:
    outcome: str
    contact_id: str = None
    opportunity_id: str = None
    message: str = ""


def load_context(client, schema):
    by_name = {f["name"]: f["id"] for f in client.list_custom_fields()}
    missing = [f.name for f in schema.fields if f.name not in by_name]
    pipeline = next((p for p in client.list_pipelines()
                     if p["name"].lower() == schema.pipeline_name.lower()), None)
    if pipeline is None:
        missing.append(f"pipeline {schema.pipeline_name}")
    if missing:
        raise SetupIncomplete("run scripts/setup_ghl.py; missing: " + ", ".join(missing))
    stage_ids = {s["name"]: s["id"] for s in pipeline.get("stages", [])}
    return Context({f.name: by_name[f.name] for f in schema.fields}, pipeline["id"], stage_ids)


def _status_date(opportunity):
    raw = opportunity.get("lastStatusChangeAt") or opportunity.get("updatedAt")
    if not raw:
        return None
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()


def _duplicate_reason(client, schema, existing, today):
    tags = {t.lower() for t in existing.get("tags") or []}
    for marker in schema.client_tags:
        if marker.lower() in tags:
            return f"existing client (tag {marker})"
    cutoff = today - dt.timedelta(days=schema.lost_window_days)
    for opp in client.search_opportunities(existing["id"], status="lost"):
        changed = _status_date(opp)
        if changed and changed >= cutoff:
            return f"deal lost on {changed.isoformat()}"
    return None


def push_lead(client, schema, ctx, payload, *, extra_tags=(), today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    try:
        validate_payload(payload, schema)
    except PayloadError as exc:
        return PushResult("rejected", message=str(exc))

    contact_id = None
    try:
        existing = client.find_duplicate(payload.get("phone"), payload.get("email"))
        if existing:
            reason = _duplicate_reason(client, schema, existing, today)
            if reason:
                client.add_tags(existing["id"], ["bot-dupe"])
                return PushResult("duplicate", existing["id"], message=reason)

        contact, is_new = client.upsert_contact(
            contact_body(payload, schema, ctx.field_ids, client.location_id))
        contact_id = contact["id"]
        client.add_tags(contact_id, lead_tags(payload, schema) + list(extra_tags))
        outcome = "created" if is_new else "updated"

        if payload["lead_tier"] == "Cold":
            return PushResult(outcome, contact_id)

        open_opps = client.search_opportunities(contact_id, pipeline_id=ctx.pipeline_id,
                                                status="open")
        if open_opps:
            return PushResult("existing", contact_id, open_opps[0]["id"])

        opp = client.create_opportunity({
            "pipelineId": ctx.pipeline_id,
            "pipelineStageId": ctx.stage_ids[schema.entry_stage],
            "locationId": client.location_id,
            "name": f"{payload['business_name']} – {payload['niche']}",
            "status": "open",
            "contactId": contact_id,
        })
        return PushResult(outcome, contact_id, opp["id"])
    except GHLAuthError:
        raise  # a scope problem fails every lead: stop the batch (spec section 7)
    except GHLError as exc:
        return PushResult("failed", contact_id, message=str(exc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_push.py -v`
Expected: 11 passed

Run: `python -m pytest`
Expected: all tests pass (7 + 11 + 6 + 14 + 11 = 49)

- [ ] **Step 5: Commit**

```bash
git add leadbot/ghl/push.py tests/test_push.py
git commit -m "feat: push_lead with duplicate guard and idempotent opportunities"
```

---

### Task 8: Test data, live push and cleanup scripts

**Files:**
- Create: `leadbot/ghl/testdata.py`, `scripts/push_test_leads.py`, `scripts/cleanup_test_leads.py`
- Test: `tests/test_testdata.py`

**Interfaces:**
- Consumes: `push_lead`, `load_context`, `PushResult` (Task 7); `SAMPLE_PAYLOAD`; client methods
- Produces:
  - `testdata.TEST_LEADS: list[dict]` (20 payloads)
  - `testdata.SEEDS: dict[str, dict]` with keys `client` and `lost`, each `{"phone": str, "name": str}`
  - `testdata.EXPECTED_FIRST_RUN: dict[str, int]`, `testdata.EXPECTED_REPEAT_RUN: dict[str, int]`
  - Ledger file `state/test_ledger.json`: `{"contacts": [ids], "opportunities": [ids], "seeds": {"client": id, "lost": id}}`

- [ ] **Step 1: Write the failing tests**

`tests/test_testdata.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_testdata.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'leadbot.ghl.testdata'`

- [ ] **Step 3: Write `leadbot/ghl/testdata.py`**

```python
"""Twenty fictional leads for the live test (spec section 8).

Phones use +1 512-555-01NN, which is reserved for fiction; emails use example.com.
Order: 6 Hot (one per niche), 6 Warm, 4 Cold, 2 duplicates, 1 email-only Warm,
1 rejected. Seeds are pre-existing contacts the two duplicates collide with.
"""

from leadbot.ghl.mapping import SAMPLE_PAYLOAD

NICHES = ["Counselors / therapists", "Roofing", "HVAC", "Electricians",
          "Personal injury lawyers", "Med spas / dental"]

SEEDS = {
    "client": {"phone": "+15125550190", "name": "Test Seed Existing Client"},
    "lost": {"phone": "+15125550191", "name": "Test Seed Lost Deal"},
}

TIER_SCORE = {"Hot": 82, "Warm": 55, "Cold": 30}


def _lead(n, tier, niche, **changes):
    p = dict(SAMPLE_PAYLOAD)
    p.update(
        business_name=f"Test Lead {n:02d} {niche}",
        phone=f"+15125550{100 + n}",
        email=f"lead{n:02d}@example.com",
        website=f"https://lead{n:02d}.example.com",
        niche=niche,
        lead_tier=tier,
        lead_score=TIER_SCORE[tier],
        bot_run_id=f"test-2026-11-04-{n:02d}",
    )
    p.update(changes)
    return p


TEST_LEADS = (
    [_lead(n, "Hot", NICHES[n - 1]) for n in range(1, 7)]
    + [_lead(n, "Warm", NICHES[(n - 7) % 6]) for n in range(7, 13)]
    + [_lead(n, "Cold", NICHES[(n - 13) % 6]) for n in range(13, 17)]
    + [_lead(17, "Hot", "HVAC", phone=SEEDS["client"]["phone"]),
       _lead(18, "Warm", "Roofing", phone=SEEDS["lost"]["phone"]),
       _lead(19, "Warm", "Electricians", phone=None),
       _lead(20, "Hot", "HVAC", phone=None, email=None)]
)

EXPECTED_FIRST_RUN = {"created": 17, "duplicate": 2, "rejected": 1}
EXPECTED_REPEAT_RUN = {"existing": 13, "updated": 4, "duplicate": 2, "rejected": 1}
```

`created: 17` = 6 Hot + 6 Warm + 4 Cold + 1 email-only Warm. On the repeat run, the 13 Hot/Warm leads find their open opportunity (`existing`) and the 4 Cold leads are `updated`.

- [ ] **Step 4: Write `scripts/push_test_leads.py`**

```python
"""Live test: seed two contacts, push the 20 test leads, compare outcome counts."""

import argparse
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from leadbot.env import client_from_env  # noqa: E402
from leadbot.ghl.push import load_context, push_lead  # noqa: E402
from leadbot.ghl.schema import load_schema  # noqa: E402
from leadbot.ghl.testdata import (  # noqa: E402
    EXPECTED_FIRST_RUN, EXPECTED_REPEAT_RUN, SEEDS, TEST_LEADS,
)

LEDGER = os.path.join(ROOT, "state", "test_ledger.json")


def load_ledger():
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as fh:
            return json.load(fh)
    return {"contacts": [], "opportunities": [], "seeds": {}}


def save_ledger(ledger):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2)


def remember(ledger, contact_id=None, opportunity_id=None):
    if contact_id and contact_id not in ledger["contacts"]:
        ledger["contacts"].append(contact_id)
    if opportunity_id and opportunity_id not in ledger["opportunities"]:
        ledger["opportunities"].append(opportunity_id)
    save_ledger(ledger)


def seed(client, schema, ctx, ledger):
    if "client" not in ledger["seeds"]:
        if not schema.client_tags:
            raise SystemExit("client_tags is empty in config/ghl_schema.toml (Task 6 step 4)")
        c, _ = client.upsert_contact({"locationId": client.location_id, "country": "US",
                                      "name": SEEDS["client"]["name"],
                                      "phone": SEEDS["client"]["phone"]})
        client.add_tags(c["id"], [schema.client_tags[0], "bot-test"])
        ledger["seeds"]["client"] = c["id"]
        remember(ledger, c["id"])
    if "lost" not in ledger["seeds"]:
        c, _ = client.upsert_contact({"locationId": client.location_id, "country": "US",
                                      "name": SEEDS["lost"]["name"],
                                      "phone": SEEDS["lost"]["phone"]})
        client.add_tags(c["id"], ["bot-test"])
        opp = client.create_opportunity({
            "pipelineId": ctx.pipeline_id, "pipelineStageId": ctx.stage_ids["Lost"],
            "locationId": client.location_id, "name": "Test Seed Lost Deal",
            "status": "lost", "contactId": c["id"]})
        ledger["seeds"]["lost"] = c["id"]
        remember(ledger, c["id"], opp["id"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", action="store_true", help="second run: expect existing/updated")
    args = ap.parse_args(argv)
    client, schema = client_from_env(), load_schema()
    ctx = load_context(client, schema)
    ledger = load_ledger()
    seed(client, schema, ctx, ledger)

    counts = Counter()
    for lead in TEST_LEADS:
        r = push_lead(client, schema, ctx, lead, extra_tags=("bot-test",))
        counts[r.outcome] += 1
        remember(ledger, r.contact_id, r.opportunity_id)
        print(f"{lead['bot_run_id']}  {lead['lead_tier']:<4}  {r.outcome:<9}  {r.message}")

    expected = EXPECTED_REPEAT_RUN if args.repeat else EXPECTED_FIRST_RUN
    print(f"got      {dict(sorted(counts.items()))}")
    print(f"expected {dict(sorted(expected.items()))}")
    if dict(counts) != expected:
        print("MISMATCH")
        return 1
    print("MATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Write `scripts/cleanup_test_leads.py`**

```python
"""Deletes every contact and opportunity in state/test_ledger.json, then verifies."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from leadbot.env import client_from_env  # noqa: E402
from leadbot.ghl.client import GHLError  # noqa: E402

LEDGER = os.path.join(ROOT, "state", "test_ledger.json")


def main():
    if not os.path.exists(LEDGER):
        print("No ledger; nothing to clean.")
        return 0
    with open(LEDGER, encoding="utf-8") as fh:
        ledger = json.load(fh)
    client = client_from_env()

    opp_ids = set(ledger["opportunities"])
    for cid in ledger["contacts"]:
        contact = client.get_contact(cid)
        if contact is None:
            continue
        if "bot-test" not in (contact.get("tags") or []):
            print(f"SKIP {cid}: no bot-test tag, not deleting")
            continue
        opp_ids.update(o["id"] for o in client.search_opportunities(cid, status="all"))
    for oid in sorted(opp_ids):
        try:
            client.delete_opportunity(oid)
        except GHLError as exc:
            if exc.status not in (400, 404):
                raise
    for cid in ledger["contacts"]:
        contact = client.get_contact(cid)
        if contact and "bot-test" in (contact.get("tags") or []):
            client.delete_contact(cid)

    left = [cid for cid in ledger["contacts"] if client.get_contact(cid) is not None]
    if left:
        print(f"STILL PRESENT: {left}")
        return 1
    os.remove(LEDGER)
    print(f"Deleted {len(ledger['contacts'])} contacts and {len(opp_ids)} opportunities. CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest -v`
Expected: all pass (49 + 5 = 54)

- [ ] **Step 7: Commit**

```bash
git add leadbot/ghl/testdata.py scripts/push_test_leads.py scripts/cleanup_test_leads.py tests/test_testdata.py
git commit -m "feat: test leads, live push and cleanup scripts"
```

---

### Task 9: Build the "[Lead Bot] Route" workflow (browser)

Claude builds this in the GHL workflow builder while the user is signed in. Before starting, the user names which rep users cover which niche; Claude reads the user list (Settings → My Staff) and writes the mapping into the build sheet.

**Files:**
- Modify: `docs/ghl-build-sheet.md`

- [ ] **Step 1: Confirm the API cannot create workflows**

Open the official API docs workflow section (`https://marketplace.gohighlevel.com/docs/ghl/workflows/get-workflow.md`). Expected: only a list endpoint. If a create endpoint exists, note it in the build sheet; the build stays in the UI either way (spec section 6).

- [ ] **Step 2: Record the rep-to-niche mapping**

Add to `docs/ghl-build-sheet.md`:
```markdown
## Reps by niche
| Niche | Reps (GHL users) |
| --- | --- |
| Counselors / therapists | <names given by the user> |
| Roofing | ... |
| HVAC | ... |
| Electricians | ... |
| Personal injury lawyers | ... |
| Med spas / dental | ... |
```
Every row is filled with real names before Step 3; a niche with no rep gets the user's chosen fallback owner.

- [ ] **Step 3: Build the workflow**

Automation → Workflows → Create → Start from scratch. Name: `[Lead Bot] Route`.
1. Trigger: Opportunity Created. Filters: Pipeline is "Bot Leads – SEO"; Status is open (so the lost seed in Task 10 does not route).
2. If/Else on contact field Niche: one branch per niche option (6 branches).
3. In each branch: Assign To User with that niche's reps, traffic split evenly (round robin). Turn on "only apply to available users" (or the equivalent option) if offered; record whether it was offered.
4. After the branches merge (or at the end of each branch): set the opportunity owner to the assigned user. Use the Update Opportunity action if it offers "Assigned to", otherwise check whether Assign To User already sets the opportunity owner. Record which worked.
5. If/Else on Lead Tier:
   - Hot: Add Task "Call: {{contact.primary_signal}}" assigned to the contact owner, due 10:00 the next business day. If the action only offers relative due times, use "1 day" and note the gap in the build sheet.
   - Warm: same task, due in 2 days.
6. Internal Notification (email or in-app) to the contact owner: subject `New {{contact.lead_tier}} lead: {{contact.company_name}}`, body containing Primary Signal and Call Opener merge fields.

Merge-field names (`{{contact.primary_signal}}` etc.) are taken from the field picker in the builder, not typed from this plan; GHL generates the keys.

- [ ] **Step 4: Save, publish, and record**

Publish the workflow. Add a "## [Lead Bot] Route" section to the build sheet listing the trigger, filters, each action as configured, and every difference from spec section 6.

- [ ] **Step 5: Commit**

```bash
git add docs/ghl-build-sheet.md
git commit -m "docs: record Route workflow as built"
```

---

### Task 10: Build the "[Lead Bot] Outcome" workflow (browser)

**Files:**
- Modify: `docs/ghl-build-sheet.md`

- [ ] **Step 1: Build the workflow**

Name: `[Lead Bot] Outcome`.
1. Trigger: Contact Changed, field Call Outcome. Filter: contact has tag `bot-lead`.
2. Find Opportunity (pipeline "Bot Leads – SEO", latest) so later opportunity steps have one in context (the 3 Nov finding).
3. If/Else on Call Outcome:
   - **No answer:** If contact has tag `bot-try-3`: Update Opportunity → stage Nurture, add tag `bot-nurture-90`. Else: add the next tag (If `bot-try-2` → add `bot-try-3`; else if `bot-try-1` → add `bot-try-2`; else add `bot-try-1`), then Add Task "Retry call" due in 2 days. Add a disabled step named `TODO part C: drip email 1`.
   - **Callback:** Add Task "Callback" due in 1 day (reps adjust the due time on the task).
   - **Interested:** Update Opportunity → stage Audit Sent. Disabled step `TODO part C: send SearchAtlas audit email`.
   - **Not interested:** Update Opportunity → stage Nurture; add tag `bot-nurture-90`. Disabled step `TODO part C: 90-day nurture`.
   - **Closed:** Update Opportunity → stage Won, status won. Disabled step `TODO part C: onboarding trigger`.
4. If the builder cannot hold a disabled step, use a Wait step or note step named the same way, and record which.

- [ ] **Step 2: Build "[Lead Bot] Lost status"**

Spec 4.4 requires the status to follow a manual move to Lost. New workflow `[Lead Bot] Lost status`: trigger Pipeline Stage Changed, pipeline "Bot Leads – SEO", stage Lost → Update Opportunity, status lost.

- [ ] **Step 3: Publish and record**

Publish both workflows. Add "## [Lead Bot] Outcome" and "## [Lead Bot] Lost status" sections to the build sheet as in Task 9 Step 4.

- [ ] **Step 4: Commit**

```bash
git add docs/ghl-build-sheet.md
git commit -m "docs: record Outcome and Lost status workflows as built"
```

---

### Task 11: Live 20-lead test and cleanup

Writes to the live sub-account. Claude shows the user the command and gets a yes before Step 1.

**Files:**
- Modify: `docs/ghl-build-sheet.md` (test results section)

- [ ] **Step 1: First push**

Run: `python scripts/push_test_leads.py`
Expected: 20 result lines, then `got {'created': 17, 'duplicate': 2, 'rejected': 1}` and `MATCH`.

- [ ] **Step 2: Check in GHL (browser)**

For each item, look in the sub-account and record pass or fail in the build sheet:
- One Hot lead: all 17 bot-written fields filled (every field except Call Outcome), tags correct, opportunity in New, owner is a rep for its niche, task "Call: …" with the Hot due time, notification received.
- One Warm lead: task due in 2 days.
- The 4 Cold leads: no opportunity; `tier-cold` tag.
- Leads 17 and 18: `bot-dupe` tag, no `bot-lead`, no new opportunity.
- Lead 19 (email only): contact and opportunity created.
- Round robin: the 2 Hot/Warm leads in each niche with 2+ reps went to different reps.
- The lost seed opportunity did not trigger Route (no task on the seed contact).

- [ ] **Step 3: Repeat push (idempotency)**

Run: `python scripts/push_test_leads.py --repeat`
Expected: `got {'duplicate': 2, 'existing': 13, 'rejected': 1, 'updated': 4}` and `MATCH`. In GHL, no Hot/Warm lead has a second opportunity.

- [ ] **Step 4: Outcome workflow check (browser)**

On test lead 01, set Call Outcome to each value in turn: No answer (4 times), Callback, Interested, Not interested, Closed. After each, check the stage, tags and task against Task 10, and record the results.

- [ ] **Step 5: Cleanup**

Run: `python scripts/cleanup_test_leads.py`
Expected: `Deleted 19 contacts and N opportunities. CLEAN` (19 = 17 created leads + 2 seeds; the rejected lead created none and the 2 duplicates are the seeds). In GHL, a contact search for tag `bot-test` shows nothing.

- [ ] **Step 6: Final verify and commit**

Run: `python scripts/setup_ghl.py --verify`
Expected: `OK` (fields, tags and pipeline untouched by cleanup).

Run: `python -m pytest`
Expected: 54 passed.

```bash
git add docs/ghl-build-sheet.md
git commit -m "docs: record live test results for part A"
```
