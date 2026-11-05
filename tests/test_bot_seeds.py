import pytest

from leadbot.bot.seeds import SeedError, city_plan, load_seeds
from leadbot.ghl.schema import load_schema

NICHE = "Counselors / therapists"


def test_austin_plan():
    p = city_plan(load_seeds(), NICHE, "Austin, TX")
    assert p.money_query == "therapist in Austin, TX"
    assert p.money_keyword == "therapist austin"
    assert p.queries == ("counseling in Austin, TX", "counseling in Round Rock, TX",
                         "counseling in Cedar Park, TX", "couples counseling Austin, TX",
                         "psychologist in Austin, TX")
    assert p.area_codes == ("512", "737")


def test_every_city_has_area_codes():
    seeds = load_seeds()
    for city in ("Austin, TX", "Dallas, TX", "Houston, TX"):
        assert city_plan(seeds, NICHE, city).area_codes


def test_seeds_default_to_dry_run():
    assert load_seeds().live is False


def test_seed_niches_exist_in_ghl_schema():
    assert set(load_seeds().niches) <= set(load_schema().niches)


def test_unknown_city_or_niche_raises():
    with pytest.raises(SeedError):
        city_plan(load_seeds(), NICHE, "Miami, FL")
    with pytest.raises(SeedError):
        city_plan(load_seeds(), "Plumbers", "Austin, TX")


def test_money_query_is_not_repeated(tmp_path):
    path = tmp_path / "seeds.toml"
    path.write_text(
        'live = false\nscraper_depth = 1\nmax_enrich_per_city = 5\nseen_days = 30\n'
        '[niches."HVAC"]\nmoney_query = "ac repair {city}"\nmoney_keyword = "ac repair {city_name}"\n'
        'queries = ["ac repair {city}", "hvac in {area}"]\n'
        '[cities."Austin, TX"]\nareas = ["Austin, TX"]\narea_codes = ["512"]\n',
        encoding="utf-8")
    p = city_plan(load_seeds(str(path)), "HVAC", "Austin, TX")
    assert p.queries == ("hvac in Austin, TX",)


SEEDS_TAIL = ('scraper_depth = 1\nmax_enrich_per_city = 5\nseen_days = 30\n'
              '[niches."HVAC"]\nmoney_query = "ac repair {city}"\nmoney_keyword = "ac {city_name}"\n'
              'queries = []\n[cities."Austin, TX"]\nareas = ["Austin, TX"]\narea_codes = ["512"]\n')


@pytest.mark.parametrize("line", ['live = "false"', 'live = "true"', "live = 1", "live = 0"])
def test_live_must_be_a_toml_boolean(tmp_path, line):
    path = tmp_path / "seeds.toml"
    path.write_text(line + "\n" + SEEDS_TAIL, encoding="utf-8")
    with pytest.raises(SeedError, match="live"):
        load_seeds(str(path))


@pytest.mark.parametrize("line,expected", [("live = true", True), ("live = false", False), ("", False)])
def test_live_boolean_values(tmp_path, line, expected):
    path = tmp_path / "seeds.toml"
    path.write_text(line + "\n" + SEEDS_TAIL, encoding="utf-8")
    assert load_seeds(str(path)).live is expected


@pytest.mark.parametrize("line", ['dnc_check = "false"', "dnc_check = 0"])
def test_dnc_check_must_be_a_toml_boolean(tmp_path, line):
    path = tmp_path / "seeds.toml"
    path.write_text(_seeds_text(line), encoding="utf-8")
    with pytest.raises(SeedError, match="dnc_check"):
        load_seeds(str(path))


@pytest.mark.parametrize("line,expected", [("dnc_check = true", True),
                                           ("dnc_check = false", False), ("", True)])
def test_dnc_check_defaults_on(tmp_path, line, expected):
    path = tmp_path / "seeds.toml"
    path.write_text(_seeds_text(line), encoding="utf-8")
    assert load_seeds(str(path)).dnc_check is expected


def _seeds_text(extra):
    return (extra + "\nscraper_depth = 1\nmax_enrich_per_city = 5\nseen_days = 30\n"
            '[niches."Counselors / therapists"]\nmoney_query = "q {city}"\nmoney_keyword = "k"\n'
            'queries = []\n[cities."Austin, TX"]\nareas = ["Austin, TX"]\narea_codes = ["512"]\n')
