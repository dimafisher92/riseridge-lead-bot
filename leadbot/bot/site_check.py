"""Homepage check for the broken-website signal (spec 3.4)."""

import re
import urllib.error
from dataclasses import dataclass

from leadbot.bot import http

TRACKING = ("googletagmanager.com", "gtag(", "fbq(", "connect.facebook.net")
_FORM = re.compile(r"<form\b", re.I)
_TEL = re.compile(r"""href\s*=\s*["']\s*tel:""", re.I)
_MAILTO = re.compile(r"mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.I)


@dataclass(frozen=True)
class SiteCheck:
    reachable: bool
    final_url: str | None = None
    has_form: bool = False
    has_tel: bool = False
    has_tracking: bool = False
    email: str | None = None
    blocked: bool = False  # the site refused the bot (401/403/429/503): not proof it is broken
    status: int | None = None


def inspect_html(final_url, html):
    lower = html.lower()
    mail = _MAILTO.search(html)
    return SiteCheck(True, final_url, bool(_FORM.search(html)), bool(_TEL.search(html)),
                     any(t in lower for t in TRACKING), mail.group(1).lower() if mail else None)


BLOCK_STATUSES = (401, 403, 429, 503)


def _failed(final_url, code):
    return SiteCheck(False, final_url, blocked=code in BLOCK_STATUSES, status=code)


def check_site(url, fetch=http.get_text):
    try:
        final_url, status, html = fetch(url)
    except urllib.error.HTTPError as exc:
        try:
            return _failed(exc.geturl() or url, exc.code)
        finally:
            exc.close()
    except Exception:  # noqa: BLE001 - DNS, refused connection, timeout or TLS: "did not load"
        return SiteCheck(False)
    if status >= 400:
        return _failed(final_url, status)
    return inspect_html(final_url, html)
