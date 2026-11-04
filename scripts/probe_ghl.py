"""Read-only probe: prints response SHAPES (keys and counts), never values."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leadbot.env import client_from_env  # noqa: E402


def keys(obj):
    return sorted(obj) if isinstance(obj, dict) else type(obj).__name__


def main():
    c = client_from_env()
    loc = c.request("GET", f"/locations/{c.location_id}")
    loc = loc.get("location", loc) if isinstance(loc, dict) else {}
    print(f"location name: {loc.get('name')}  (confirm this is the RiseRidge sub-account)")
    pipelines = c.list_pipelines()
    print(f"pipelines: {len(pipelines)}; pipeline keys: {keys(pipelines[0]) if pipelines else '-'}")
    if pipelines and pipelines[0].get("stages"):
        print(f"stage keys: {keys(pipelines[0]['stages'][0])}")
    fields = c.list_custom_fields()
    print(f"custom fields: {len(fields)}; field keys: {keys(fields[0]) if fields else '-'}")
    tags = c.list_tags()
    print(f"tags: {len(tags)}; tag keys: {keys(tags[0]) if tags else '-'}")
    raw = c.request("GET", "/contacts/search/duplicate",
                    query={"locationId": c.location_id, "number": "+15125550199"})
    print(f"duplicate search (no match expected) top-level keys: {keys(raw)}")
    print("PROBE OK")


if __name__ == "__main__":
    main()
