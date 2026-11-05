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


def test_seen_mark_moves_last_seen(tmp_path):
    path = str(tmp_path / "seen.sqlite")
    s = SeenStore(path)
    s.mark("p1", dt.date(2026, 1, 1), "Cold")
    s.mark("p1", dt.date(2026, 11, 1), "Warm")
    s.close()
    assert SeenStore(path).recently_seen("p1", dt.date(2026, 11, 20), 30)
