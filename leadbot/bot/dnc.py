"""National Do Not Call lookup from FTC area-code files in state/dnc/ (spec 4)."""

import os
import re
from dataclasses import dataclass, field

from leadbot.env import ROOT

DEFAULT_DNC_DIR = os.path.join(ROOT, "state", "dnc")


@dataclass
class DncList:
    numbers: set = field(default_factory=set)

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
        with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                number = _ten_digits(line)
                if number:
                    dnc.numbers.add(number)
    return dnc


def is_listed(dnc, e164):
    number = _ten_digits(e164)
    return bool(number) and number in dnc.numbers


def missing_area_codes(dnc, area_codes):
    have = dnc.area_codes
    return [code for code in area_codes if code not in have]
