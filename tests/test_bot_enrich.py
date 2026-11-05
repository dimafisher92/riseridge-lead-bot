import datetime as dt

from leadbot.bot.domain_age import registrable_domain, registration_date
from leadbot.bot.pagespeed import mobile_score
from leadbot.bot.site_check import SiteCheck, check_site

GOOD = ('<html><head><script async src="https://www.googletagmanager.com/gtag/js?id=G-X">'
        '</script></head><body><a href="tel:+15125550132">Call</a><form action="/c"></form>'
        '<a href="mailto:Hello@BrightPath.example.com">Email</a></body></html>')


def test_good_homepage():
    r = check_site("https://x.example.com/", fetch=lambda url: (url, 200, GOOD))
    assert r == SiteCheck(True, "https://x.example.com/", True, True, True,
                          "hello@brightpath.example.com")


def test_bare_homepage():
    r = check_site("https://x.example.com/", fetch=lambda url: (url, 200, "<html><body>Hi</body></html>"))
    assert (r.reachable, r.has_form, r.has_tel, r.has_tracking, r.email) == (True, False, False, False, None)


def test_meta_pixel_counts_as_tracking():
    html = "<script>fbq('init', '1');</script>"
    assert check_site("https://x.example.com/", fetch=lambda url: (url, 200, html)).has_tracking


def test_error_status_is_unreachable():
    assert check_site("https://x.example.com/", fetch=lambda url: (url, 404, "")).reachable is False


def test_network_error_is_unreachable():
    def boom(url):
        raise OSError("timed out")
    assert check_site("https://x.example.com/", fetch=boom) == SiteCheck(False)


def test_pagespeed_score_and_request():
    seen = []

    def fake(url):
        seen.append(url)
        return {"lighthouseResult": {"categories": {"performance": {"score": 0.37}}}}
    assert mobile_score("https://x.example.com/a?b=1", get_json=fake) == 37
    assert "strategy=mobile" in seen[0]
    assert "url=https%3A%2F%2Fx.example.com%2Fa%3Fb%3D1" in seen[0]


def test_pagespeed_error_is_unknown():
    def boom(url):
        raise OSError("429")
    assert mobile_score("https://x.example.com/", get_json=boom) is None


def test_registrable_domain():
    assert registrable_domain("https://www.brightpath.example.com/x") == "example.com"
    assert registrable_domain("https://www.calmtherapy.com/") == "calmtherapy.com"
    assert registrable_domain("") is None


def test_registration_date():
    seen = []

    def fake(url):
        seen.append(url)
        return {"events": [{"eventAction": "last changed", "eventDate": "2026-01-01T00:00:00Z"},
                           {"eventAction": "registration", "eventDate": "2026-08-01T12:00:00Z"}]}
    assert registration_date("https://www.calmtherapy.com/", get_json=fake) == dt.date(2026, 8, 1)
    assert seen == ["https://rdap.org/domain/calmtherapy.com"]


def test_registration_error_is_unknown():
    def boom(url):
        raise OSError("404")
    assert registration_date("https://www.calmtherapy.com/", get_json=boom) is None
