"""PageSpeed Insights mobile performance score, no API key (spec 3.4)."""

import urllib.parse

from leadbot.bot import http

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def mobile_score(url, get_json=http.get_json):
    query = urllib.parse.urlencode({"url": url, "strategy": "mobile", "category": "performance"})
    try:
        score = get_json(f"{PSI_URL}?{query}")["lighthouseResult"]["categories"]["performance"]["score"]
    except Exception:  # noqa: BLE001 - quota, timeout or shape change all mean "unknown"
        return None
    return None if score is None else round(score * 100)
