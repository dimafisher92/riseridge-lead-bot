"""National Do Not Call lookup from FTC area-code files in state/dnc/ (spec 4)."""

import datetime as dt
import os
import re
from dataclasses import dataclass, field

from leadbot.env import ROOT

DEFAULT_DNC_DIR = os.path.join(ROOT, "state", "dnc")
# FTC scrub list must be no older than 31 days; not legal advice.
DNC_MAX_AGE_DAYS = 31


@dataclass
class DncList:
    numbers: set = field(default_factory=set)
    oldest_file_date: dt.date | None = None  # oldest modification date among loaded files

    @property
    def area_codes(self):
        return {n[:3] for n in self.numbers}


def _ten_digits(text):
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def load_dnc(directory=DEFAULT_DNC_DIR):
    dnc = DncList()
    if not os.path.isdir(directory):
        return dnc
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith((".txt", ".csv")):
            continue
        path = os.path.join(directory, name)
        modified = dt.date.fromtimestamp(os.path.getmtime(path))
        if dnc.oldest_file_date is None or modified < dnc.oldest_file_date:
            dnc.oldest_file_date = modified
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                number = _ten_digits(line)
                if number:
                    dnc.numbers.add(number)
    return dnc


def area_code(phone):
    number = _ten_digits(phone)
    return number[:3] if number else None


def is_listed(dnc, e164):
    number = _ten_digits(e164)
    return bool(number) and number in dnc.numbers


def missing_area_codes(dnc, area_codes):
    have = dnc.area_codes
    return [code for code in area_codes if code not in have]
