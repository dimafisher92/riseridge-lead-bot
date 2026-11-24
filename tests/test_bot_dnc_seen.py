import datetime as dt

from leadbot.bot.dnc import is_listed, load_dnc, missing_area_codes
from leadbot.bot.seen import SeenStore


def test_dnc_loads_ten_digit_numbers(tmp_path):
    d = tmp_path / "dnc"
    d.mkdir()
    (d / "512.txt").write_text("5125550101\n512,5550102\n1-512-555-0103\nheader line\n",
                               encoding="utf-8")
    (d / "notes.md").write_text("5125550199\n", encoding="utf-8")
    dnc = load_dnc(str(d))
    assert dnc.numbers == {"5125550101", "5125550102", "5125550103"}
    assert is_listed(dnc, "+15125550102")
    assert not is_listed(dnc, "+15125550199")
    assert not is_listed(dnc, None)
    assert missing_area_codes(dnc, ("512", "737")) == ["737"]


def test_missing_dnc_dir_is_empty(tmp_path):
    assert load_dnc(str(tmp_path / "none")).numbers == set()


def test_seen_window():
    s = SeenStore(":memory:")
    s.mark("p1", dt.date(2026, 11, 1), "Hot")
    assert s.recently_seen("p1", dt.date(2026, 11, 30), 30)
    assert not s.recently_seen("p1", dt.date(2026, 12, 1), 30)
    assert not s.recently_seen("other", dt.date(2026, 11, 2), 30)
    s.close()


def test_seen_mark_moves_last_seen(tmp_path):
    path = str(tmp_path / "seen.sqlite")
    s = SeenStore(path)
    s.mark("p1", dt.date(2026, 1, 1), "Cold")
    s.mark("p1", dt.date(2026, 11, 1), "Warm")
    s.close()
    reopened = SeenStore(path)
    assert reopened.recently_seen("p1", dt.date(2026, 11, 20), 30)
    reopened.close()


def test_dnc_records_oldest_loaded_file_date(tmp_path):
    import os
    d = tmp_path / "dnc"
    d.mkdir()
    (d / "512.txt").write_text("5125550101\n", encoding="utf-8")
    (d / "737.csv").write_text("7375550101\n", encoding="utf-8")
    (d / "notes.md").write_text("old notes\n", encoding="utf-8")
    stamp = dt.datetime(2026, 10, 1, 12, 0).timestamp()
    os.utime(d / "737.csv", (stamp, stamp))
    os.utime(d / "512.txt", (stamp + 5 * 86400, stamp + 5 * 86400))
    older = dt.datetime(2026, 1, 1, 12, 0).timestamp()
    os.utime(d / "notes.md", (older, older))  # not a DNC file: ignored
    assert load_dnc(str(d)).oldest_file_date == dt.date(2026, 10, 1)
    assert load_dnc(str(tmp_path / "none")).oldest_file_date is None


def test_area_code_of_a_phone():
    from leadbot.bot.dnc import area_code
    assert area_code("+15125550102") == "512"
    assert area_code("(737) 555-0101") == "737"
    assert area_code(None) is None and area_code("555-0101") is None


def test_failed_row_is_retried_and_a_later_mark_overwrites_it():
    store = SeenStore(":memory:")
    today = dt.date(2026, 11, 6)
    store.mark("p1", today, "failed")
    assert not store.recently_seen("p1", today, 30)
    assert store.last_outcome("p1") == "failed"
    assert store.last_outcome("nope") is None
    store.mark("p1", today, "created")
    assert store.recently_seen("p1", today, 30)
    assert store.last_outcome("p1") == "created"
    store.close()
