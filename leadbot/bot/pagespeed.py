"""PageSpeed Insights mobile performance score, no API key (spec 3.4)."""

import urllib.parse

from leadbot.bot import http

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def mobile_score(url, get_json=http.get_json):
    query = urllib.parse.urlencode({"url": url, "strategy": "mobile", "category": "performance"})
    try:
        score = get_json(f"{PSI_URL}?{query}")["lighthouseResult"]["categories"]["performance"]["score"]
        if score is None or isinstance(score, bool) or not isinstance(score, (int, float)):
            return None
        return round(score * 100)
    except Exception:  # noqa: BLE001 - quota, timeout or shape change all mean "unknown"
        return None
