"""Runs gosom's Google Maps scraper and normalises its JSON Lines (spec 3.2)."""

import datetime as dt
import json
import os
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class ScrapeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Place:
    place_id: str
    name: str
    category: str
    categories: tuple
    phone: str | None
    website: str | None
    gbp_url: str | None
    owner_claimed: bool
    status: str
    review_count: int
    rating: float | None
    review_dates: tuple
    email: str | None

    @property
    def closed(self):
        return "closed" in self.status.lower()


def to_e164(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def clean_website(url):
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", "", ""))


def _review_date(review):
    raw = review.get("published_at")
    if raw:
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    micros = review.get("posted_at_unix_micros")
    if micros:
        return dt.datetime.fromtimestamp(micros / 1_000_000, dt.timezone.utc).date()
    return None


def parse_row(row):
    owner = row.get("owner") or {}
    emails = row.get("emails") or []
    dates = [d for d in (_review_date(r) for r in row.get("user_reviews") or []) if d]
    return Place(
        place_id=row.get("place_id") or "",
        name=(row.get("title") or "").strip(),
        category=row.get("category") or "",
        categories=tuple(row.get("categories") or ()),
        phone=to_e164(row.get("phone")),
        website=clean_website(row.get("web_site")),
        gbp_url=row.get("link") or None,
        owner_claimed=bool(owner.get("id")),
        status=row.get("status") or "",
        review_count=int(row.get("review_count") or 0),
        rating=float(row["review_rating"]) if row.get("review_rating") else None,
        review_dates=tuple(sorted(dates)),
        email=emails[0].lower() if emails else None,
    )


class Places(list):
    """Places read from one results file; bad_rows counts rows parse_row could not read."""

    def __init__(self, places=(), bad_rows=0):
        super().__init__(places)
        self.bad_rows = bad_rows


def read_results(path):
    places = Places()
    if not os.path.exists(path):
        return places
    with open(path, encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    for i, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            # A killed run can leave a truncated last line; corruption earlier is an error.
            if i == len(lines) - 1:
                break
            raise ScrapeError(f"malformed JSON on line {i + 1} of {path}") from exc
        try:
            place = parse_row(row)
        except (ValueError, TypeError, KeyError, AttributeError):
            places.bad_rows += 1  # one odd row never sinks the file
            continue
        if place.place_id and place.name:
            places.append(place)
    return places


def run_scraper(queries, out_dir, name, *, exe, depth, runner=subprocess.run, timeout=3 * 3600):
    os.makedirs(out_dir, exist_ok=True)
    query_file = os.path.join(out_dir, f"{name}.queries.txt")
    results = os.path.join(out_dir, f"{name}.raw.jsonl")
    log = os.path.join(out_dir, f"{name}.scraper.log")
    with open(query_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(queries) + "\n")
    if os.path.exists(results):
        os.remove(results)
    args = [exe, "-input", query_file, "-results", results, "-json", "-depth", str(depth),
            "-c", "1", "-lang", "en", "-exit-on-inactivity", "3m"]
    with open(log, "w", encoding="utf-8") as fh:
        try:
            proc = runner(args, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ScrapeError(f"scraper timed out after {timeout}s; see {log}") from exc
    if proc.returncode != 0:
        raise ScrapeError(f"scraper exited {proc.returncode}; see {log}")
    return read_results(results)
