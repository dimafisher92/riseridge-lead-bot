"""Score, tier and primary signal from the SOP Step 4 table (spec 3.6)."""

import os
import tomllib
from dataclasses import dataclass

from leadbot.env import ROOT

DEFAULT_SCORING_PATH = os.path.join(ROOT, "config", "scoring.toml")

# SOP Step 4 table order; also breaks ties for the primary signal.
SIGNAL_ORDER = ("gbp_unverified_or_suspended", "google_ads_spend", "meta_ads_active",
                "broken_website", "new_business", "rank_gap", "technical_debt", "competitor_gap")


@dataclass(frozen=True)
class Scoring:
    weights: dict
    hot: int
    warm: int
    retention_niches: tuple


def load_scoring(path=DEFAULT_SCORING_PATH):
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    return Scoring(dict(raw["weights"]), int(raw["tiers"]["hot"]), int(raw["tiers"]["warm"]),
                   tuple(raw.get("retention_niches", ())))


def lead_score(true_signals, niche, scoring, ads_spend=None):
    weights = scoring.weights
    total = sum(weights[s] for s in SIGNAL_ORDER if s in true_signals)
    if "google_ads_spend" in true_signals and ads_spend and ads_spend > 3000:
        total += weights["google_ads_spend_over_3k_bonus"]
    if niche in scoring.retention_niches:
        total += weights["retention_niche"]
    return max(0, min(total, 100))


def lead_tier(score, scoring):
    if score >= scoring.hot:
        return "Hot"
    if score >= scoring.warm:
        return "Warm"
    return "Cold"


def primary_flag(true_signals, scoring):
    present = [s for s in SIGNAL_ORDER if s in true_signals]
    if not present:
        return None
    return max(present, key=lambda s: scoring.weights[s])  # max keeps the first of equals
