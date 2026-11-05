import dataclasses
import datetime as dt
import json

import pytest

from leadbot.bot import cli
from leadbot.bot.dnc import DncList
from leadbot.bot.seeds import load_seeds
from leadbot.bot.seen import SeenStore
from leadbot.ghl.client import GHLAuthError
from leadbot.ghl.schema import load_schema
from leadbot.ghl.setup import run_setup
from tests.fakes import FakeClient

RUN_ID = "2026-11-06-counselors-therapists-austin-tx"
# Area codes 512 and 737 loaded, none of the test candidates listed.
FRESH_DNC = DncList({"5125550199", "7375550199"}, oldest_file_date=dt.date.today())


def candidate(n=1, **over):
    c = {"business_name": "Bright Path Counseling (fictional)", "phone": "+15125550132",
         "email": None, "website": "https://brightpath.example.com/",
         "niche": "Counselors / therapists", "city": "Austin, TX",
         "gbp_url": "https://www.google.com/maps/place/?q=place_id:fx-brightpath",
         "gbp_status": "Unverified", "review_count": 4, "rating": 5.0,
         "money_keyword": "therapist austin", "current_rank": None,
         "signals": ["gbp_unverified_or_suspended", "new_business"],
         "primary_signal": "GBP Suspended", "lead_score": 45, "lead_tier": "Warm",
         "audit_summary": "", "call_opener": "", "bot_run_id": f"{RUN_ID}-{n:04d}",
         "evidence": {"gbp_unverified_or_suspended": "Google listing has no owner (unclaimed)"},
         "unknown": [], "place_id": f"fx-{n}"}
    c.update(over)
    return c


def qualified(c, **over):
    q = dict(c, audit_summary="Google listing has no owner.\nOnly 4 reviews, all from the last two months.",
             call_opener="Hi, I noticed Bright Path Counseling's Google listing has no owner, "
                         "so it may be missing calls.")
    q.update(over)
    return q


def write_run(tmp_path, cands, quals, dnc_checked=True, dropped=()):
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps({"run_id": RUN_ID, "dnc_checked": dnc_checked}),
                                         encoding="utf-8")
    for name, rows in (("candidates.jsonl", cands), ("qualified.jsonl", quals),
                       ("dropped.jsonl", dropped)):
        (run_dir / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return run_dir


def push(run_dir, *extra):
    return cli.main(["push", str(run_dir / "qualified.jsonl"), *extra])


def report_of(run_dir, name):
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def test_run_id():
    assert cli.run_id_for(dt.date(2026, 11, 6), "Counselors / therapists", "Austin, TX") == RUN_ID


def test_dry_run_validates_without_ghl(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "client_from_env", lambda: pytest.fail("dry run touched GHL"))
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)])
    assert push(run_dir, "--dry-run") == 0
    assert report_of(run_dir, "dry_run_report.json")["counts"] == {"would_push": 1}


def test_changed_score_is_rejected(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c, lead_score=90)])
    assert push(run_dir, "--dry-run") == 1
    result = report_of(run_dir, "dry_run_report.json")["results"][0]
    assert result["outcome"] == "rejected" and "lead_score" in result["message"]


def test_unknown_bot_run_id_is_rejected(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(candidate(n=7))])
    assert push(run_dir, "--dry-run") == 1


def test_live_refused_while_seeds_not_live(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)])
    with pytest.raises(SystemExit, match="live = false"):
        push(run_dir)


def test_live_refused_for_no_dnc_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), live=True))
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)], dnc_checked=False)
    with pytest.raises(SystemExit, match="--no-dnc"):
        push(run_dir)


def live_env(monkeypatch, tmp_path):
    schema = load_schema()
    client = FakeClient()
    run_setup(client, schema)
    client.create_pipeline("02. GSM Appointment New Client", ["New client!"])
    for f in schema.fields:
        if f.created_by == "ui":
            client.fields.append({"id": "ui-" + f.name, "name": f.name, "dataType": f.data_type,
                                  "picklistOptions": list(f.options)})
    seen_path = str(tmp_path / "seen.sqlite")
    monkeypatch.setattr(cli, "client_from_env", lambda: client)
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), live=True))
    monkeypatch.setattr(cli, "SEEN_PATH", seen_path)
    monkeypatch.setattr(cli, "load_dnc", lambda: FRESH_DNC)
    return client, seen_path


def test_live_push_creates_lead_and_marks_seen(tmp_path, monkeypatch):
    client, seen_path = live_env(monkeypatch, tmp_path)
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)], dropped=[
        {"place_id": "fx-closed", "business_name": "x", "reason": "closed"},
        {"place_id": "fx-cap", "business_name": "y", "reason": "over_nightly_cap"},
        {"place_id": "fx-ghl", "business_name": "z", "reason": "ghl_lookup_failed"}])
    assert push(run_dir) == 0
    assert report_of(run_dir, "push_report.json")["counts"] == {"created": 1}
    assert len(client.opportunities) == 1
    store, today = SeenStore(seen_path), dt.date.today()
    assert store.recently_seen("fx-1", today, 30)
    assert store.recently_seen("fx-closed", today, 30)
    assert not store.recently_seen("fx-cap", today, 30)
    assert not store.recently_seen("fx-ghl", today, 30)
    store.close()


def test_collect_refuses_without_dnc_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_dnc", lambda: cli.DncList())
    with pytest.raises(SystemExit, match="512"):
        cli.main(["collect", "--niche", "Counselors / therapists", "--city", "Austin, TX"])


def test_auth_error_aborts_batch(tmp_path, monkeypatch):
    client, seen_path = live_env(monkeypatch, tmp_path)
    cands = [candidate(n, phone=f"+151255501{n:02d}") for n in (1, 2, 3)]
    run_dir = write_run(tmp_path, cands, [qualified(c) for c in cands])
    real_push_lead, calls = cli.push_lead, []

    def flaky_push_lead(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return real_push_lead(*args, **kwargs)
        raise GHLAuthError(401, "token revoked")

    monkeypatch.setattr(cli, "push_lead", flaky_push_lead)
    assert push(run_dir) == 3
    assert len(calls) == 2
    assert report_of(run_dir, "push_report.json")["counts"] == {"created": 1, "aborted": 2}
    store = SeenStore(seen_path)
    try:
        assert store.recently_seen("fx-1", dt.date.today(), 30)
        assert not store.recently_seen("fx-2", dt.date.today(), 30)
        assert not store.recently_seen("fx-3", dt.date.today(), 30)
    finally:
        store.close()


def test_seen_marks_only_handled_leads(tmp_path, monkeypatch):
    client, seen_path = live_env(monkeypatch, tmp_path)
    pushed = candidate(1)
    cold = candidate(2, lead_tier="Cold", lead_score=10, email=None)
    rejected = candidate(3)
    unqualified = candidate(4, phone="+15125550104")
    run_dir = write_run(tmp_path, [pushed, cold, rejected, unqualified],
                        [qualified(pushed), qualified(rejected, lead_score=90)])
    assert push(run_dir) == 1
    assert report_of(run_dir, "push_report.json")["counts"] == {"created": 1, "rejected": 1}
    store = SeenStore(seen_path)
    try:
        today = dt.date.today()
        assert store.recently_seen("fx-1", today, 30)
        assert store.recently_seen("fx-2", today, 30)
        assert not store.recently_seen("fx-3", today, 30)
        assert not store.recently_seen("fx-4", today, 30)
        outcomes = dict(store.db.execute("SELECT place_id, last_outcome FROM seen").fetchall())
        assert outcomes == {"fx-1": "created", "fx-2": "cold_no_email"}
    finally:
        store.close()


def test_collect_refuses_stale_dnc_files(tmp_path, monkeypatch):
    stale = DncList({"5125550199", "7375550199"},
                    oldest_file_date=dt.date.today() - dt.timedelta(days=40))
    monkeypatch.setattr(cli, "load_dnc", lambda: stale)
    monkeypatch.setattr(cli, "client_from_env", lambda: pytest.fail("stale DNC reached GHL"))
    with pytest.raises(SystemExit, match="40 days old"):
        cli.main(["collect", "--niche", "Counselors / therapists", "--city", "Austin, TX"])


def test_live_push_rejects_dnc_listed_and_unloaded_area(tmp_path, monkeypatch):
    client, _ = live_env(monkeypatch, tmp_path)
    listed = candidate(1, phone="+15125550199")
    dallas = candidate(2, phone="+12145550102")
    ok = candidate(3, phone="+15125550103")
    run_dir = write_run(tmp_path, [listed, dallas, ok], [qualified(c) for c in (listed, dallas, ok)])
    real_push_lead, pushed = cli.push_lead, []

    def spy(client_, schema, ctx, payload, **kw):
        pushed.append(payload["bot_run_id"])
        return real_push_lead(client_, schema, ctx, payload, **kw)

    monkeypatch.setattr(cli, "push_lead", spy)
    assert push(run_dir) == 1
    results = {r["bot_run_id"]: r for r in report_of(run_dir, "push_report.json")["results"]}
    outcome = {k: (r["outcome"], r["message"]) for k, r in results.items()}
    assert outcome[listed["bot_run_id"]] == ("rejected", "dnc")
    assert outcome[dallas["bot_run_id"]] == ("rejected", "dnc_area_not_loaded")
    assert results[ok["bot_run_id"]]["outcome"] == "created"
    assert pushed == [ok["bot_run_id"]]
