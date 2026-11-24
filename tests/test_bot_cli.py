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


SUMMARY = "Google listing has no owner.\nOnly 4 reviews, all from the last two months."
OPENER = ("Hi, I noticed Bright Path Counseling's Google listing has no owner, "
          "so it may be missing calls.")


def qualified(c, **over):
    """A qualified.jsonl line: the bot_run_id and the two texts Claude writes, nothing else."""
    q = {"bot_run_id": c["bot_run_id"], "audit_summary": SUMMARY, "call_opener": OPENER}
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


def test_dry_run_validates_the_merged_record(tmp_path, monkeypatch):
    validated = []
    monkeypatch.setattr(cli, "validate_payload", lambda payload, schema: validated.append(payload))
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)])
    assert push(run_dir, "--dry-run") == 0
    payload = validated[0]
    assert (payload["audit_summary"], payload["call_opener"]) == (SUMMARY, OPENER)
    assert payload["lead_score"] == c["lead_score"] and payload["phone"] == c["phone"]
    assert "place_id" not in payload and "evidence" not in payload


def test_any_field_besides_the_texts_is_rejected(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c, lead_score=c["lead_score"])])
    assert push(run_dir, "--dry-run") == 1
    result = report_of(run_dir, "dry_run_report.json")["results"][0]
    assert result["outcome"] == "rejected" and "unexpected field lead_score" in result["message"]


def test_line_for_a_lead_that_needs_no_text_is_rejected(tmp_path):
    cold = candidate(lead_tier="Cold", lead_score=10, email=None)
    run_dir = write_run(tmp_path, [cold], [qualified(cold)])
    assert push(run_dir, "--dry-run") == 1
    result = report_of(run_dir, "dry_run_report.json")["results"][0]
    assert (result["outcome"], result["message"]) == ("rejected", "not eligible")


def test_second_line_for_the_same_lead_is_rejected(tmp_path, monkeypatch):
    _, seen_path = live_env(monkeypatch, tmp_path)
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c), qualified(c, call_opener="Hi again.")])
    assert push(run_dir) == 1
    results = report_of(run_dir, "push_report.json")["results"]
    assert [(r["outcome"], r["message"]) for r in results][1] == ("rejected", "duplicate line")
    assert results[0]["outcome"] == "created"
    store = SeenStore(seen_path)
    try:
        assert store.recently_seen("fx-1", dt.date.today(), 30)  # first line's outcome counts
    finally:
        store.close()


def test_line_that_is_not_an_object_is_rejected(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [["not", "an", "object"], qualified(c)])
    assert push(run_dir, "--dry-run") == 1
    outcomes = [r["outcome"] for r in report_of(run_dir, "dry_run_report.json")["results"]]
    assert outcomes == ["rejected", "would_push"]


def test_unknown_bot_run_id_is_rejected(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(candidate(n=7))])
    assert push(run_dir, "--dry-run") == 1


def test_live_refused_while_seeds_not_live(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), live=False))
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)])
    with pytest.raises(SystemExit, match="live = false"):
        push(run_dir)


def test_live_refused_for_no_dnc_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), live=True, dnc_check=True))
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
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), live=True, dnc_check=True))
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
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), dnc_check=True))
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
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), dnc_check=True))
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


def test_auth_error_loading_context_aborts_every_line(tmp_path, monkeypatch):
    live_env(monkeypatch, tmp_path)

    def no_context(client, schema):
        raise GHLAuthError(401, "token revoked")

    monkeypatch.setattr(cli, "load_context", no_context)
    monkeypatch.setattr(cli, "push_lead", lambda *a, **k: pytest.fail("pushed after auth error"))
    cands = [candidate(n, phone=f"+151255501{n:02d}") for n in (1, 2)]
    run_dir = write_run(tmp_path, cands, [qualified(c) for c in cands])
    assert push(run_dir) == 3
    assert report_of(run_dir, "push_report.json")["counts"] == {"aborted": 2}


def test_network_error_fails_one_lead_and_continues(tmp_path, monkeypatch):
    live_env(monkeypatch, tmp_path)
    cands = [candidate(n, phone=f"+151255501{n:02d}") for n in (1, 2, 3)]
    run_dir = write_run(tmp_path, cands, [qualified(c) for c in cands])
    real_push_lead = cli.push_lead

    def flaky(client, schema, ctx, payload, **kw):
        if payload["bot_run_id"].endswith("-0002"):
            raise TimeoutError("timed out")
        return real_push_lead(client, schema, ctx, payload, **kw)

    monkeypatch.setattr(cli, "push_lead", flaky)
    assert push(run_dir) == 1
    results = report_of(run_dir, "push_report.json")["results"]
    assert [r["outcome"] for r in results] == ["created", "failed", "created"]
    assert "timed out" in results[1]["message"]


def test_collect_auth_error_exits_3(tmp_path, monkeypatch, capsys):
    from leadbot.bot.collect import Deps
    from leadbot.bot.scrape import read_results
    from tests.test_bot_collect import FIXTURE

    def find_contact(phone, email):
        raise GHLAuthError(401, "token revoked")

    places = read_results(FIXTURE)
    monkeypatch.setattr(cli, "load_dnc", lambda: FRESH_DNC)
    monkeypatch.setattr(cli, "client_from_env", lambda: object())
    monkeypatch.setattr(cli, "_real_deps", lambda run_dir, depth, client: Deps(
        scrape=lambda queries, name: places, site_check=lambda url: None,
        pagespeed=lambda url: None, domain_age=lambda url: None, find_contact=find_contact))
    monkeypatch.setattr(cli, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli, "SEEN_PATH", str(tmp_path / "seen.sqlite"))
    assert cli.main(["collect", "--niche", "Counselors / therapists", "--city", "Austin, TX",
                     "--today", "2026-11-06"]) == 3
    out = capsys.readouterr().out
    assert "GHL" in out and len(out.strip().splitlines()) == 1


def test_seen_uses_the_run_date(tmp_path, monkeypatch):
    _, seen_path = live_env(monkeypatch, tmp_path)
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)], dropped=[
        {"place_id": "fx-closed", "business_name": "x", "reason": "closed"}])
    assert push(run_dir) == 0
    store = SeenStore(seen_path)
    try:
        days = dict(store.db.execute("SELECT place_id, last_seen FROM seen").fetchall())
    finally:
        store.close()
    assert days == {"fx-1": "2026-11-06", "fx-closed": "2026-11-06"}  # from the run_id prefix


def test_seen_falls_back_to_today_without_a_dated_run_id(tmp_path, monkeypatch):
    _, seen_path = live_env(monkeypatch, tmp_path)
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)])
    (run_dir / "report.json").write_text(json.dumps({"run_id": "manual", "dnc_checked": True}),
                                         encoding="utf-8")
    assert push(run_dir) == 0
    store = SeenStore(seen_path)
    try:
        days = dict(store.db.execute("SELECT place_id, last_seen FROM seen").fetchall())
    finally:
        store.close()
    assert days == {"fx-1": dt.date.today().isoformat()}


def test_unexpected_error_fails_one_lead_and_continues(tmp_path, monkeypatch):
    live_env(monkeypatch, tmp_path)
    cands = [candidate(n, phone=f"+151255501{n:02d}") for n in (1, 2, 3)]
    run_dir = write_run(tmp_path, cands, [qualified(c) for c in cands])
    real_push_lead = cli.push_lead

    def flaky(client, schema, ctx, payload, **kw):
        if payload["bot_run_id"].endswith("-0001"):
            raise KeyError("opportunities")
        return real_push_lead(client, schema, ctx, payload, **kw)

    monkeypatch.setattr(cli, "push_lead", flaky)
    assert push(run_dir) == 1
    results = report_of(run_dir, "push_report.json")["results"]
    assert [r["outcome"] for r in results] == ["failed", "created", "created"]
    assert results[0]["message"].startswith("KeyError")
    store = SeenStore(str(tmp_path / "seen.sqlite"))
    try:
        assert store.last_outcome("fx-1") == "failed"
        assert store.last_outcome("fx-2") == "created"
    finally:
        store.close()


def test_malformed_qualified_line_is_rejected_and_the_rest_processed(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [])
    (run_dir / "qualified.jsonl").write_text(
        '{"bot_run_id": "oops' + chr(10) + json.dumps(qualified(c)) + chr(10), encoding="utf-8")
    assert push(run_dir, "--dry-run") == 1
    results = report_of(run_dir, "dry_run_report.json")["results"]
    assert [(r["outcome"], r["message"]) for r in results] == [
        ("rejected", "malformed line 1"), ("would_push", "")]


def test_live_push_refuses_stale_dnc_files(tmp_path, monkeypatch):
    live_env(monkeypatch, tmp_path)
    stale = DncList({"5125550199", "7375550199"},
                    oldest_file_date=dt.date.today() - dt.timedelta(days=40))
    monkeypatch.setattr(cli, "load_dnc", lambda: stale)
    monkeypatch.setattr(cli, "client_from_env", lambda: pytest.fail("stale DNC reached GHL"))
    monkeypatch.setattr(cli, "push_lead", lambda *a, **k: pytest.fail("pushed with stale DNC"))
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)])
    with pytest.raises(SystemExit, match="40 days old"):
        push(run_dir)
    assert not (run_dir / "push_report.json").exists()


def test_dnc_area_not_loaded_drop_is_not_marked_seen(tmp_path, monkeypatch):
    _, seen_path = live_env(monkeypatch, tmp_path)
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)], dropped=[
        {"place_id": "fx-dallas", "business_name": "d", "reason": "dnc_area_not_loaded"},
        {"place_id": "fx-dnc", "business_name": "n", "reason": "dnc"}])
    assert push(run_dir) == 0
    store = SeenStore(seen_path)
    try:
        assert not store.recently_seen("fx-dallas", dt.date.today(), 30)
        assert store.recently_seen("fx-dnc", dt.date.today(), 30)
    finally:
        store.close()


def test_failed_push_is_retried_next_night_end_to_end(tmp_path, monkeypatch):
    from leadbot.bot.collect import Deps, collect_city
    from leadbot.bot.scrape import read_results
    from leadbot.bot.seeds import city_plan
    from leadbot.bot.score import load_scoring
    from tests.test_bot_collect import FIXTURE, RUN_ID as COLLECT_RUN
    client, seen_path = live_env(monkeypatch, tmp_path)
    sunrise = next(p for p in read_results(FIXTURE) if p.place_id == "fx-sunrise")
    c = candidate(1, phone=sunrise.phone, place_id="fx-sunrise")
    run_dir = write_run(tmp_path, [c], [qualified(c)])

    def boom(body):
        raise KeyError("opportunity")

    monkeypatch.setattr(client, "create_opportunity", boom)
    assert push(run_dir) == 1
    assert len(client.contacts) == 1 and not client.opportunities  # contact without a deal
    monkeypatch.undo()
    live_env(monkeypatch, tmp_path)  # undo also dropped the cli patches; client is a new fake
    monkeypatch.setattr(cli, "client_from_env", lambda: client)

    seeds = load_seeds()
    plan = city_plan(seeds, "Counselors / therapists", "Austin, TX")
    deps = Deps(scrape=lambda queries, name: [sunrise], site_check=lambda url: None,
                pagespeed=lambda url: None, domain_age=lambda url: None,
                find_contact=lambda phone, email: next(
                    (x for x in client.contacts.values() if x.get("phone") == phone), None))
    seen = SeenStore(seen_path)
    try:
        report = collect_city(plan, seeds, load_scoring(), load_schema(), deps, run_id="2026-11-07-x",
                              run_dir=str(tmp_path / "run2"), today=dt.date.today(), seen=seen)
    finally:
        seen.close()
    assert report["dropped"] == {} and report["scraped"] == 1
    rows = [json.loads(x) for x in (tmp_path / "run2" / "candidates.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [r["place_id"] for r in rows] == ["fx-sunrise"]
    retry = dict(rows[0], lead_tier="Warm")  # eligible for text, as a lead with signals would be
    cand_path = tmp_path / "run2" / "candidates.jsonl"
    cand_path.write_text(json.dumps(retry) + chr(10), encoding="utf-8")
    (tmp_path / "run2" / "qualified.jsonl").write_text(
        json.dumps(qualified(retry)) + chr(10), encoding="utf-8")
    (tmp_path / "run2" / "report.json").write_text(
        json.dumps({"run_id": "2026-11-07-x", "dnc_checked": True}), encoding="utf-8")
    assert cli.main(["push", str(tmp_path / "run2" / "qualified.jsonl")]) == 0
    assert len(client.opportunities) == 1 and len(client.contacts) == 1
    store = SeenStore(seen_path)
    try:
        assert store.last_outcome("fx-sunrise") in ("created", "updated")
    finally:
        store.close()


def test_qualified_file_with_a_bad_byte_rejects_only_that_line(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [])
    nl, bom, bad = bytes([10]), bytes([0xEF, 0xBB, 0xBF]), bytes([0xFF])
    (run_dir / "qualified.jsonl").write_bytes(
        bom + json.dumps(qualified(c)).encode() + nl + b'{"x": "' + bad + b'"}' + nl)
    assert push(run_dir, "--dry-run") == 1
    results = report_of(run_dir, "dry_run_report.json")["results"]
    assert [(r["outcome"], r["message"]) for r in results] == [
        ("would_push", ""), ("rejected", "malformed line 2")]


def test_dnc_off_live_push_skips_dnc_files(tmp_path, monkeypatch):
    client, _ = live_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "load_seeds",
                        lambda: dataclasses.replace(load_seeds(), live=True, dnc_check=False))
    monkeypatch.setattr(cli, "load_dnc", lambda: pytest.fail("loaded DNC with dnc_check off"))
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)], dnc_checked=False)
    assert push(run_dir) == 0
    assert len(client.opportunities) == 1


def test_dnc_off_collect_skips_dnc_files(tmp_path, monkeypatch):
    from leadbot.bot.collect import Deps
    from leadbot.bot.scrape import read_results
    from tests.test_bot_collect import FIXTURE

    places = read_results(FIXTURE)
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), dnc_check=False))
    monkeypatch.setattr(cli, "load_dnc", lambda: pytest.fail("loaded DNC with dnc_check off"))
    monkeypatch.setattr(cli, "client_from_env", lambda: object())
    monkeypatch.setattr(cli, "_real_deps", lambda run_dir, depth, client: Deps(
        scrape=lambda queries, name: places, site_check=lambda url: None,
        pagespeed=lambda url: None, domain_age=lambda url: None,
        find_contact=lambda phone, email: None))
    monkeypatch.setattr(cli, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli, "SEEN_PATH", str(tmp_path / "seen.sqlite"))
    assert cli.main(["collect", "--niche", "Counselors / therapists", "--city", "Austin, TX",
                     "--today", "2026-11-06"]) == 0
