"""Mutual-funds package constants: plan/option vocab, parsing patterns, label column, ISIN regex."""

from __future__ import annotations

import re
from typing import Literal

Plan = Literal["Direct", "Regular"]
FundOption = Literal["Growth", "IDCW", "Bonus", "ETF", "Other"]

LABEL_COL = "shortName"

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")

NOISE_PATTERNS = [
    r"\s*\(\d+\)\s*$",
    r"[\s\-]*\(direct\s+plan\)\s*$",
    r"[\s\-]*\(regular\s+plan\)\s*$",
    r"[\s\-]*direct\s+plan\s*$",
    r"[\s\-]*regular\s+plan\s*$",
    r"[\s\-]*growth\s+option\s*$",
    r"[\s\-]*growth\s+plan\s*$",
    r"[\s\-]*idcw(\s+(reinvestment|payout))?\s*$",
    r"[\s\-]*growth\s*$",
    r"[\s\-]+direct\s*$",
    r"[\s\-]+regular\s*$",
]

DIRECT_RE = re.compile(r"\bdirect(\s+plan)?\b", re.IGNORECASE)
REGULAR_RE = re.compile(r"\bregular(\s+plan)?\b", re.IGNORECASE)
IDCW_RE = re.compile(r"(?:idcw|income\s+distribution|\bdividend\b)", re.IGNORECASE)

GROWTH_PHRASE_RE = re.compile(r"\bgrowth\s+(option|plan)\b", re.IGNORECASE)
GROWTH_BETWEEN_DASHES_RE = re.compile(r"-\s*growth\s*-", re.IGNORECASE)
GROWTH_END_RE = re.compile(r"(?:^|[\s\-])growth\s*$", re.IGNORECASE)
CUMULATIVE_RE = re.compile(r"\bcumulative\b", re.IGNORECASE)

BONUS_RE = re.compile(r"\bbonus\b", re.IGNORECASE)
ETF_RE = re.compile(r"\betf\b", re.IGNORECASE)
