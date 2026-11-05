"""Seeds: which Maps queries to run for one niche in one city (spec 3.1)."""

import os
import tomllib
from dataclasses import dataclass

from leadbot.env import ROOT

DEFAULT_SEEDS_PATH = os.path.join(ROOT, "config", "seeds.toml")


class SeedError(ValueError):
    pass


@dataclass(frozen=True)
class Seeds:
    live: bool
    depth: int
    max_enrich: int
    seen_days: int
    niches: dict
    cities: dict
    dnc_check: bool = True


@dataclass(frozen=True)
class CityPlan:
    niche: str
    city: str
    money_query: str
    money_keyword: str
    queries: tuple
    area_codes: tuple


def load_seeds(path=DEFAULT_SEEDS_PATH):
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    live = raw.get("live", False)
    if not isinstance(live, bool):  # live = "false" must not switch pushes on
        raise SeedError(f"live must be true or false (a TOML boolean), not {live!r}")
    dnc_check = raw.get("dnc_check", True)  # missing key = check on (fail safe)
    if not isinstance(dnc_check, bool):
        raise SeedError(f"dnc_check must be true or false (a TOML boolean), not {dnc_check!r}")
    return Seeds(live is True, int(raw["scraper_depth"]),
                 int(raw["max_enrich_per_city"]), int(raw["seen_days"]),
                 dict(raw["niches"]), dict(raw["cities"]), dnc_check)


def city_plan(seeds, niche, city):
    if niche not in seeds.niches:
        raise SeedError(f"niche {niche!r} is not in seeds.toml")
    if city not in seeds.cities:
        raise SeedError(f"city {city!r} is not in seeds.toml")
    n, c = seeds.niches[niche], seeds.cities[city]
    fmt = {"city": city, "city_name": city.split(",")[0].strip()}
    money_query = n["money_query"].format(**fmt)
    queries = []
    for template in n["queries"]:
        for area in (c["areas"] if "{area}" in template else [None]):
            query = template.format(area=area, **fmt)
            if query != money_query and query not in queries:
                queries.append(query)
    return CityPlan(niche, city, money_query, n["money_keyword"].format(**fmt).lower(),
                    tuple(queries), tuple(c["area_codes"]))
