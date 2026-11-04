"""Create missing GHL objects (default), preview (--dry-run) or check (--verify)."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leadbot.env import client_from_env  # noqa: E402
from leadbot.ghl.schema import load_schema  # noqa: E402
from leadbot.ghl.setup import run_setup  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    report = run_setup(client_from_env(), load_schema(),
                       dry_run=args.dry_run, verify_only=args.verify)
    label = "would create" if args.dry_run else "created"
    for line in report.created:
        print(f"{label}: {line}")
    print(f"already there: {len(report.existing)}")
    for name in report.missing_ui:
        print(f"build in the GHL UI: field {name}")
    for line in report.drift:
        print(f"DRIFT: {line}")
    print("OK" if report.ok else "NOT OK")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
