"""Signal detection with evidence (spec 3.5). States: TRUE, FALSE, UNKNOWN."""

import datetime as dt
from dataclasses import dataclass

TRUE, FALSE, UNKNOWN = "TRUE", "FALSE", "UNKNOWN"
NOT_IN_B1 = ("google_ads_spend", "meta_ads_active", "technical_debt")
NEW_DOMAIN_DAYS = 183
NEW_REVIEW_DAYS = 92
NEW_MAX_REVIEWS = 15
SLOW_PAGESPEED = 40
MAP_PACK = 3
NEAR_MISS_MAX = 20
INACTIVE_DAYS = 730
_BRAND_CUTS = (" - ", " | ", " – ", " (")


@dataclass(frozen=True)
class Signal:
    state: str
    evidence: str


@dataclass(frozen=True)
class Enrichment:
    site: object = None  # SiteCheck, or None when the site was not checked
    pagespeed: int | None = None
    registered: dt.date | None = None


@dataclass(frozen=True)
class RankInfo:
    keyword: str
    checked: int  # places returned for the money keyword
    position: int | None  # 1-based; None when the place is not in those results
    top3_avg_reviews: float | None


def brand_key(name):
    """Lowercased name cut at the first " - ", " | ", " – " or " (" (a location suffix)."""
    key = name.lower()
    cuts = [key.index(c) for c in _BRAND_CUTS if c in key]
    return (key[:min(cuts)] if cuts else key).strip()


def chain_keys(places):
    """Brand keys that appear on more than one listing among the places scraped in a run."""
    counts = {}
    for place in places:
        key = brand_key(place.name)
        counts[key] = counts.get(key, 0) + 1
    return frozenset(key for key, n in counts.items() if n > 1)


def rank_info(place_id, money_places, keyword, chain_keys=frozenset()):
    position = next((i for i, p in enumerate(money_places, 1) if p.place_id == place_id), None)
    rivals = [p.review_count for p in money_places
              if p.place_id != place_id and brand_key(p.name) not in chain_keys][:3]
    average = sum(rivals) / len(rivals) if rivals else None
    return RankInfo(keyword, len(money_places), position, average)


def gbp_status(place):
    return "Verified" if place.owner_claimed else "Unverified"


def _gbp(place):
    if place.owner_claimed:
        return Signal(FALSE, "Google listing is owner-claimed")
    return Signal(TRUE, "Google listing has no owner (unclaimed)")


def _site(place, enrich):
    if not place.website:
        return Signal(TRUE, "no website on the Google listing")
    site = enrich.site
    if site is None:
        return Signal(UNKNOWN, "website not checked")
    if site.blocked:
        return Signal(UNKNOWN, f"website refused the bot's request (HTTP {site.status})")
    if not site.reachable:
        code = f" (HTTP {site.status})" if site.status else ""
        return Signal(TRUE, f"website {place.website} did not load{code}")
    fails = []
    if not site.has_form:
        fails.append("no lead form")
    if not site.has_tel:
        fails.append("no click-to-call link")
    if not site.has_tracking:
        fails.append("no conversion tag (gtag/GTM/Meta Pixel)")  # information only
    slow = enrich.pagespeed is not None and enrich.pagespeed < SLOW_PAGESPEED
    if slow:
        fails.append(f"mobile PageSpeed {enrich.pagespeed}")
    if (not site.has_form and not site.has_tel) or slow:
        return Signal(TRUE, "homepage: " + ", ".join(fails))
    return Signal(FALSE, "homepage: " + (", ".join(fails) or "form, call link and tag present"))


def _new(place, enrich, today):
    if enrich.registered and (today - enrich.registered).days < NEW_DOMAIN_DAYS:
        return Signal(TRUE, f"domain registered {enrich.registered.isoformat()}")
    count, dates = place.review_count, place.review_dates
    if count > NEW_MAX_REVIEWS:
        return Signal(FALSE, f"{count} reviews")
    if dates and count >= 1:
        oldest = dates[0]
        if (today - oldest).days > NEW_REVIEW_DAYS:
            return Signal(FALSE, f"oldest visible review {oldest.isoformat()}")
        # Maps shows only some reviews; an older hidden one would make this a false TRUE.
        if len(dates) < count:
            return Signal(UNKNOWN, f"only {len(dates)} of {count} reviews visible")
        return Signal(TRUE, f"{count} reviews, oldest {oldest.isoformat()}")
    if enrich.registered:
        return Signal(FALSE, f"domain registered {enrich.registered.isoformat()}")
    if count:
        return Signal(UNKNOWN, "no review dates and no domain date")
    return Signal(UNKNOWN, "no reviews and no domain date")


def _rank(rank):
    kw = rank.keyword
    if rank.checked == 0:
        return Signal(UNKNOWN, f"no Maps results for '{kw}'")
    pos = rank.position
    if pos is None:
        return Signal(UNKNOWN, f"not in the top {rank.checked} on Maps for '{kw}'")
    if pos <= MAP_PACK:
        return Signal(FALSE, f"#{pos} on Maps for '{kw}' (map pack)")
    if pos <= NEAR_MISS_MAX:
        return Signal(TRUE, f"#{pos} on Maps for '{kw}', just outside the map pack")
    return Signal(FALSE, f"#{pos} on Maps for '{kw}'")


def _gap(place, rank):
    if rank.position is None:
        return Signal(UNKNOWN, f"not in the results for '{rank.keyword}'")
    average = rank.top3_avg_reviews
    if average is None:
        return Signal(UNKNOWN, "no independent competitors to compare")
    text = f"top 3 for '{rank.keyword}' average {average:.0f} reviews vs {place.review_count}"
    return Signal(TRUE if average >= 3 * max(place.review_count, 1) else FALSE, text)


def negatives(place, today):
    """Signals that lower the score. Never GHL flags, so they are kept apart from detect()."""
    dates = place.review_dates
    if not dates:
        return {"inactive_listing": Signal(UNKNOWN, "no review dates")}
    # Maps shows only a sample of the reviews; an unseen newer one would make TRUE wrong.
    if len(dates) < place.review_count:
        return {"inactive_listing": Signal(
            UNKNOWN, f"only {len(dates)} of {place.review_count} reviews visible")}
    newest = dates[-1]
    if (today - newest).days > INACTIVE_DAYS:
        return {"inactive_listing": Signal(TRUE, f"newest visible review {newest.isoformat()}, "
                                                 "over 2 years ago")}
    return {"inactive_listing": Signal(FALSE, f"newest visible review {newest.isoformat()}")}


def detect(place, enrich, rank, today):
    signals = {
        "gbp_unverified_or_suspended": _gbp(place),
        "broken_website": _site(place, enrich),
        "new_business": _new(place, enrich, today),
        "rank_gap": _rank(rank),
        "competitor_gap": _gap(place, rank),
    }
    for name in NOT_IN_B1:
        signals[name] = Signal(UNKNOWN, "not checked in B1")
    return signals


def true_flags(signals):
    return {name for name, s in signals.items() if s.state == TRUE}
