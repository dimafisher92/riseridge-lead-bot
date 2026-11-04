"""Deletes test contacts and opportunities, verifies they are gone, then reports.

The ledger is removed only when the cleanup is clean. Exit 1 otherwise.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from leadbot.env import client_from_env  # noqa: E402
from leadbot.ghl.cleanup import cleanup_test_data  # noqa: E402

LEDGER = os.path.join(ROOT, "state", "test_ledger.json")


def main():
    ledger_exists = os.path.exists(LEDGER)
    ledger = {"contacts": [], "opportunities": [], "seeds": {}}
    if ledger_exists:
        with open(LEDGER, encoding="utf-8") as fh:
            ledger = json.load(fh)
    result = cleanup_test_data(client_from_env(), ledger)

    print(f"contacts deleted:      {len(result.contacts_deleted)}")
    print(f"opportunities deleted: {len(result.opportunities_deleted)}")
    for cid in result.skipped:
        print(f"SKIP {cid}: no bot-test tag, not deleted")
    for line in result.leftovers:
        print(f"LEFTOVER {line}")
    if not result.clean:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2)
        print("NOT CLEAN: ledger kept")
        return 1
    if ledger_exists:
        os.remove(LEDGER)
    print("CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
