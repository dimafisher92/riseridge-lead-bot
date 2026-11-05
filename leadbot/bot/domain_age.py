"""Domain registration date through RDAP (spec 3.4)."""

import datetime as dt
from urllib.parse import urlsplit

from leadbot.bot import http

RDAP_URL = "https://rdap.org/domain/{}"


def registrable_domain(url):
    host = (urlsplit(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else None


def registration_date(url, get_json=http.get_json):
    domain = registrable_domain(url)
    if not domain:
        return None
    try:
        data = get_json(RDAP_URL.format(domain))
    except Exception:  # noqa: BLE001 - unknown registry, rate limit or timeout
        return None
    for event in data.get("events") or []:
        if event.get("eventAction") == "registration" and event.get("eventDate"):
            try:
                return dt.datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00")).date()
            except ValueError:
                return None
    return None
