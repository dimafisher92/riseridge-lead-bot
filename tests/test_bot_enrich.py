import datetime as dt
import urllib.error

import pytest

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
    r = check_site("https://x.example.com/", fetch=lambda url: (url, 404, ""))
    assert r == SiteCheck(False, "https://x.example.com/", status=404)


@pytest.mark.parametrize("code", [401, 403, 429, 503])
def test_refusal_statuses_are_blocked(code):
    r = check_site("https://x.example.com/", fetch=lambda url: (url, code, ""))
    assert r == SiteCheck(False, "https://x.example.com/", blocked=True, status=code)


def test_http_error_403_is_blocked():
    def refuse(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    r = check_site("https://x.example.com/", fetch=refuse)
    assert (r.reachable, r.blocked, r.status) == (False, True, 403)


def test_http_error_404_is_not_blocked():
    def gone(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    r = check_site("https://x.example.com/", fetch=gone)
    assert (r.reachable, r.blocked, r.status) == (False, False, 404)


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


def test_pagespeed_odd_shape_is_unknown():
    def score(value):
        return lambda url: {"lighthouseResult": {"categories": {"performance": {"score": value}}}}
    assert mobile_score("https://x.example.com/", get_json=score("fast")) is None
    assert mobile_score("https://x.example.com/", get_json=lambda url: ["not", "a", "dict"]) is None
    assert mobile_score("https://x.example.com/", get_json=score(None)) is None


def test_registration_odd_shape_is_unknown():
    url = "https://www.calmtherapy.com/"
    assert registration_date(url, get_json=lambda u: ["not a dict"]) is None
    assert registration_date(url, get_json=lambda u: {"events": "nope"}) is None
    assert registration_date(url, get_json=lambda u: {"events": [None, 3]}) is None
    assert registration_date(url, get_json=lambda u: {"events": [
        {"eventAction": "registration", "eventDate": 20260801}]}) is None
