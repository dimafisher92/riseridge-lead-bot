import datetime as dt
import json
import os
import shutil
import subprocess

import pytest

from leadbot.bot.scrape import ScrapeError, clean_website, read_results, run_scraper, to_e164

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "maps_rows.jsonl")


def by_name(places, prefix):
    return next(p for p in places if p.name.startswith(prefix))


def test_reads_rows_and_skips_rows_without_place_id():
    places = read_results(FIXTURE)
    assert len(places) == 6
    assert [p.place_id for p in places][:2] == ["fx-lakeview", "fx-brightpath"]


def test_unclaimed_new_listing_is_normalised():
    p = by_name(read_results(FIXTURE), "Bright Path")
    assert p.owner_claimed is False
    assert p.phone == "+15125550132"
    assert p.website == "https://brightpath.example.com/"
    assert p.review_count == 4
    assert p.rating == 5.0
    assert p.review_dates == (dt.date(2026, 9, 20), dt.date(2026, 10, 2), dt.date(2026, 10, 25))
    assert p.email is None


def test_claimed_closed_and_email():
    places = read_results(FIXTURE)
    assert by_name(places, "Lakeview").owner_claimed is True
    assert by_name(places, "Oak Hill").closed is True
    assert by_name(places, "Lakeview").closed is False
    assert by_name(places, "Quiet Mind").owner_claimed is False
    assert by_name(places, "Quiet Mind").phone is None
    assert by_name(places, "Cedar").email == "hello@cedar.example.com"


def test_missing_results_file_reads_as_empty(tmp_path):
    assert read_results(str(tmp_path / "none.jsonl")) == []


@pytest.mark.parametrize("raw,expected", [
    ("+1 512-555-0132", "+15125550132"), ("(512) 555-0132", "+15125550132"),
    ("15125550132", "+15125550132"), ("555-0132", None), ("", None), (None, None)])
def test_to_e164(raw, expected):
    assert to_e164(raw) == expected


def test_clean_website():
    assert clean_website("https://BrightPath.example.com/?utm_source=gbp") == \
        "https://brightpath.example.com/"
    assert clean_website("https://sunrise.example.com/austin") == "https://sunrise.example.com/austin"
    assert clean_website("brightpath.example.com") is None
    assert clean_website("") is None


class FakeRunner:
    def __init__(self, returncode=0):
        self.returncode, self.args = returncode, None

    def __call__(self, args, stdout, stderr, timeout):
        self.args = args
        shutil.copy(FIXTURE, args[args.index("-results") + 1])
        return subprocess.CompletedProcess(args, self.returncode)


def test_run_scraper_writes_queries_and_reads_results(tmp_path):
    runner = FakeRunner()
    places = run_scraper(["therapist in Austin, TX"], str(tmp_path), "money",
                         exe="gmaps.exe", depth=5, runner=runner)
    assert len(places) == 6
    assert (tmp_path / "money.queries.txt").read_text(encoding="utf-8") == "therapist in Austin, TX\n"
    a = runner.args
    assert a[0] == "gmaps.exe"
    assert a[a.index("-depth") + 1] == "5" and a[a.index("-c") + 1] == "1"
    assert "-json" in a and a[a.index("-exit-on-inactivity") + 1] == "3m"


def test_run_scraper_nonzero_exit_raises(tmp_path):
    with pytest.raises(ScrapeError):
        run_scraper(["q"], str(tmp_path), "money", exe="gmaps.exe", depth=1,
                    runner=FakeRunner(returncode=1))


def fixture_lines():
    with open(FIXTURE, encoding="utf-8") as fh:
        return fh.read().splitlines()


def test_truncated_last_line_is_skipped(tmp_path):
    path = tmp_path / "r.jsonl"
    lines = fixture_lines()
    path.write_text("\n".join(lines[:2]) + '\n{"title": "Cut', encoding="utf-8")
    places = read_results(str(path))
    assert [p.place_id for p in places] == ["fx-lakeview", "fx-brightpath"]


def test_malformed_middle_line_raises(tmp_path):
    path = tmp_path / "r.jsonl"
    lines = fixture_lines()
    path.write_text(lines[0] + "\n{bad json\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(ScrapeError, match="line 2"):
        read_results(str(path))


class TimeoutRunner:
    def __call__(self, args, stdout, stderr, timeout):
        raise subprocess.TimeoutExpired(args, timeout)


def test_run_scraper_timeout_raises_scrape_error(tmp_path):
    with pytest.raises(ScrapeError, match="timed out"):
        run_scraper(["q"], str(tmp_path), "money", exe="gmaps.exe", depth=1,
                    runner=TimeoutRunner(), timeout=60)


def test_run_scraper_clears_stale_results(tmp_path):
    lines = fixture_lines()
    (tmp_path / "money.raw.jsonl").write_text(lines[0] + "\n", encoding="utf-8")

    def silent_runner(args, stdout, stderr, timeout):
        return subprocess.CompletedProcess(args, 0)

    assert run_scraper(["q"], str(tmp_path), "money", exe="gmaps.exe", depth=1,
                       runner=silent_runner) == []


def test_unparseable_rows_are_skipped_and_counted(tmp_path):
    path = tmp_path / "r.jsonl"
    lines = fixture_lines()
    path.write_text("\n".join([lines[0], '{"place_id": "fx-x", "title": "X", "review_count": "lots"}',
                               '[1, 2]', '{"place_id": "fx-y", "title": "Y", "review_rating": "n/a"}',
                               lines[1]]) + "\n", encoding="utf-8")
    places = read_results(str(path))
    assert [p.place_id for p in places] == ["fx-lakeview", "fx-brightpath"]
    assert places.bad_rows == 3
    assert read_results(FIXTURE).bad_rows == 0


def test_run_scraper_passes_bad_row_count(tmp_path):
    lines = fixture_lines()

    def runner(args, stdout, stderr, timeout):
        with open(args[args.index("-results") + 1], "w", encoding="utf-8") as fh:
            fh.write(lines[0] + '\n{"place_id": "fx-x", "title": "X", "review_count": "lots"}\n')
        return subprocess.CompletedProcess(args, 0)

    places = run_scraper(["q"], str(tmp_path), "money", exe="gmaps.exe", depth=1, runner=runner)
    assert len(places) == 1 and places.bad_rows == 1
