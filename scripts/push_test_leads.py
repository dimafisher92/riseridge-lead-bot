"""Live test: seed two contacts, push the 20 test leads, compare outcome counts."""

import argparse
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from leadbot.env import client_from_env  # noqa: E402
from leadbot.ghl.push import load_context, push_lead  # noqa: E402
from leadbot.ghl.schema import load_schema  # noqa: E402
from leadbot.ghl.testdata import (  # noqa: E402
    EXPECTED_FIRST_RUN, EXPECTED_REPEAT_RUN, SEEDS, TEST_LEADS,
)

LEDGER = os.path.join(ROOT, "state", "test_ledger.json")


def load_ledger():
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as fh:
            return json.load(fh)
    return {"contacts": [], "opportunities": [], "seeds": {}}


def save_ledger(ledger):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2)


def remember(ledger, contact_id=None, opportunity_id=None):
    if contact_id and contact_id not in ledger["contacts"]:
        ledger["contacts"].append(contact_id)
    if opportunity_id and opportunity_id not in ledger["opportunities"]:
        ledger["opportunities"].append(opportunity_id)
    save_ledger(ledger)


def seed(client, schema, ctx, ledger):
    if "client" not in ledger["seeds"]:
        if not schema.client_tags:
            raise SystemExit("client_tags is empty in config/ghl_schema.toml (Task 6 step 4)")
        c, _ = client.upsert_contact({"locationId": client.location_id, "country": "US",
                                      "name": SEEDS["client"]["name"],
                                      "phone": SEEDS["client"]["phone"]})
        remember(ledger, c["id"])
        client.add_tags(c["id"], [schema.client_tags[0], "bot-test"])
        ledger["seeds"]["client"] = c["id"]
        remember(ledger, c["id"])
    if "lost" not in ledger["seeds"]:
        c, _ = client.upsert_contact({"locationId": client.location_id, "country": "US",
                                      "name": SEEDS["lost"]["name"],
                                      "phone": SEEDS["lost"]["phone"]})
        remember(ledger, c["id"])
        client.add_tags(c["id"], ["bot-test"])
        opp = client.create_opportunity({
            "pipelineId": ctx.pipeline_id, "pipelineStageId": ctx.stage_ids["Lost"],
            "locationId": client.location_id, "name": "Test Seed Lost Deal",
            "status": "lost", "contactId": c["id"]})
        ledger["seeds"]["lost"] = c["id"]
        remember(ledger, c["id"], opp["id"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", action="store_true", help="second run: expect existing/updated")
    args = ap.parse_args(argv)
    client, schema = client_from_env(), load_schema()
    ctx = load_context(client, schema)
    ledger = load_ledger()
    seed(client, schema, ctx, ledger)

    counts = Counter()
    for lead in TEST_LEADS:
        r = push_lead(client, schema, ctx, lead, extra_tags=("bot-test",))
        counts[r.outcome] += 1
        remember(ledger, r.contact_id, r.opportunity_id)
        print(f"{lead['bot_run_id']}  {lead['lead_tier']:<4}  {r.outcome:<9}  {r.message}")

    expected = EXPECTED_REPEAT_RUN if args.repeat else EXPECTED_FIRST_RUN
    print(f"got      {dict(sorted(counts.items()))}")
    print(f"expected {dict(sorted(expected.items()))}")
    if dict(counts) != expected:
        print("MISMATCH")
        return 1
    print("MATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
