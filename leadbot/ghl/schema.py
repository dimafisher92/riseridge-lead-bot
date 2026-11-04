"""Loads config/ghl_schema.toml: the one place GHL names, options and tags live."""

import os
import re
import tomllib
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SCHEMA_PATH = os.path.join(ROOT, "config", "ghl_schema.toml")

OPTION_TYPES = frozenset({"SINGLE_OPTIONS", "MULTIPLE_OPTIONS", "CHECKBOX", "RADIO"})

TIER_FIELD = "Lead Tier"
NICHE_FIELD = "Niche"
PRIMARY_FIELD = "Primary Signal"
ALL_SIGNALS_FIELD = "All Signals"


class SchemaError(ValueError):
    pass


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tier_tag(tier):
    return "tier-" + slug(tier)


def niche_tag(niche):
    return "niche-" + slug(niche)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    data_type: str
    source: str
    options: tuple = ()

    @property
    def created_by(self):
        return "ui" if self.data_type in OPTION_TYPES else "api"


@dataclass(frozen=True)
class Schema:
    pipeline_name: str
    stages: tuple
    entry_stage: str
    client_tags: tuple
    lost_window_days: int
    static_tags: tuple
    fields: tuple
    signals: dict
    signal_tags: dict

    def field(self, name):
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    @property
    def tiers(self):
        return self.field(TIER_FIELD).options

    @property
    def niches(self):
        return self.field(NICHE_FIELD).options

    @property
    def categories(self):
        return tuple(self.signal_tags)

    def all_tags(self):
        tags = list(self.static_tags)
        tags += [tier_tag(t) for t in self.tiers]
        tags += [niche_tag(n) for n in self.niches]
        tags += list(self.signal_tags.values())
        seen, out = set(), []
        for t in tags:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out


def load_schema(path=DEFAULT_SCHEMA_PATH):
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    p = raw["pipeline"]
    fields = tuple(
        FieldSpec(f["name"], f["data_type"], f.get("source", ""), tuple(f.get("options", ())))
        for f in raw.get("fields", [])
    )
    schema = Schema(
        pipeline_name=p["name"],
        stages=tuple(p["stages"]),
        entry_stage=p["entry_stage"],
        client_tags=tuple(raw["duplicate"]["client_tags"]),
        lost_window_days=int(raw["duplicate"]["lost_window_days"]),
        static_tags=tuple(raw["tags"]["static"]),
        fields=fields,
        signals=dict(raw["signals"]),
        signal_tags=dict(raw["signal_tags"]),
    )
    _validate(schema)
    return schema


def _validate(schema):
    if schema.entry_stage not in schema.stages:
        raise SchemaError(f"entry_stage {schema.entry_stage!r} is not one of the pipeline stages")
    if not schema.fields:
        return
    for category in schema.signals.values():
        if category not in schema.signal_tags:
            raise SchemaError(f"signal category {category!r} has no entry in [signal_tags]")
    for name in (PRIMARY_FIELD, ALL_SIGNALS_FIELD):
        if set(schema.field(name).options) != set(schema.signal_tags):
            raise SchemaError(f"{name} options must equal the [signal_tags] categories")
