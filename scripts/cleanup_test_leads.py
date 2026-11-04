"""Deletes every contact and opportunity in state/test_ledger.json, then verifies."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from leadbot.env import client_from_env  # noqa: E402
from leadbot.ghl.client import GHLError  # noqa: E402

LEDGER = os.path.join(ROOT, "state", "test_ledger.json")


def main():
    if not os.path.exists(LEDGER):
        print("No ledger; nothing to clean.")
        return 0
    with open(LEDGER, encoding="utf-8") as fh:
        ledger = json.load(fh)
    client = client_from_env()

    opp_ids = set(ledger["opportunities"])
    for cid in ledger["contacts"]:
        contact = client.get_contact(cid)
        if contact is None:
            continue
        if "bot-test" not in (contact.get("tags") or []):
            print(f"SKIP {cid}: no bot-test tag, not deleting")
            continue
        opp_ids.update(o["id"] for o in client.search_opportunities(cid, status="all"))
    for oid in sorted(opp_ids):
        try:
            client.delete_opportunity(oid)
        except GHLError as exc:
            if exc.status not in (400, 404):
                raise
    for cid in ledger["contacts"]:
        contact = client.get_contact(cid)
        if contact and "bot-test" in (contact.get("tags") or []):
            client.delete_contact(cid)

    left = [cid for cid in ledger["contacts"] if client.get_contact(cid) is not None]
    if left:
        print(f"STILL PRESENT: {left}")
        return 1
    os.remove(LEDGER)
    print(f"Deleted {len(ledger['contacts'])} contacts and {len(opp_ids)} opportunities. CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
