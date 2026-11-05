"""Small stdlib HTTP helpers for enrichment (spec 3.4)."""

import json
import urllib.request

from leadbot.ghl.client import USER_AGENT

MAX_BYTES = 2_000_000


def get_text(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/html,application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), resp.status, raw.decode(charset, errors="replace")


def get_json(url, timeout=60):
    _, _, text = get_text(url, timeout)
    return json.loads(text)
