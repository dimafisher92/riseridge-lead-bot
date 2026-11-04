"""Reads .env into os.environ (real environment wins) and builds the client."""

import os

from leadbot.ghl.client import GHLClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def client_from_env(root=ROOT):
    load_dotenv(os.path.join(root, ".env"))
    missing = [k for k in ("GHL_TOKEN", "GHL_LOCATION_ID") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}")
    return GHLClient(os.environ["GHL_TOKEN"], os.environ["GHL_LOCATION_ID"])
