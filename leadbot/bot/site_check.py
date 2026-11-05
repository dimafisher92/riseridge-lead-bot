"""Homepage check for the broken-website signal (spec 3.4)."""

import re
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


def inspect_html(final_url, html):
    lower = html.lower()
    mail = _MAILTO.search(html)
    return SiteCheck(True, final_url, bool(_FORM.search(html)), bool(_TEL.search(html)),
                     any(t in lower for t in TRACKING), mail.group(1).lower() if mail else None)


def check_site(url, fetch=http.get_text):
    try:
        final_url, status, html = fetch(url)
    except Exception:  # noqa: BLE001 - any network failure means "did not load"
        return SiteCheck(False)
    if status >= 400:
        return SiteCheck(False, final_url)
    return inspect_html(final_url, html)
