"""Mutual-funds package constants: scheme-name parsing patterns, label column, ISIN regex."""

from __future__ import annotations

import re

# Column preferred for chart/cluster labels (mutual_funds.correlation_analytics).
LABEL_COL = "shortName"

# Valid ISIN: 2 letters, 9 alphanumerics, 1 check digit (mutual_funds.holdings).
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")

# Trailing plan/option suffixes stripped for display (mutual_funds.short_scheme_name).
# Each pattern is end-anchored; the caller loops until the name stops shrinking so
# multi-suffix tails like "- Direct Plan - Growth - IDCW" peel off layer by layer.
# Anchoring avoids eating valid mid-name occurrences (e.g. "Axis Growth Opportunities Fund").
NOISE_PATTERNS = [
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

# Plan / option detection from scheme-name strings (mutual_funds.detect_plan/detect_option).
DIRECT_RE = re.compile(r"\bdirect(\s+plan)?\b", re.IGNORECASE)
REGULAR_RE = re.compile(r"\bregular(\s+plan)?\b", re.IGNORECASE)
# IDCW also matches "IDCWOption" (no space) and "Dividend".
IDCW_RE = re.compile(r"(?:idcw|income\s+distribution|\bdividend\b)", re.IGNORECASE)

# Growth-as-payout-option detection — checked in order:
#   1. Phrase "Growth Option"/"Growth Plan" anywhere — strong signal.
#   2. Token "Growth" sandwiched between dashes (e.g. "...- Growth - Direct Plan").
#   3. Token "Growth" at end of name (with or without leading dash/whitespace).
# None match "Axis Growth Opportunities Fund" because "Growth" is followed by letters.
GROWTH_PHRASE_RE = re.compile(r"\bgrowth\s+(option|plan)\b", re.IGNORECASE)
GROWTH_BETWEEN_DASHES_RE = re.compile(r"-\s*growth\s*-", re.IGNORECASE)
GROWTH_END_RE = re.compile(r"(?:^|[\s\-])growth\s*$", re.IGNORECASE)
# "Cumulative" is a legacy term for the Growth payout option.
CUMULATIVE_RE = re.compile(r"\bcumulative\b", re.IGNORECASE)

BONUS_RE = re.compile(r"\bbonus\b", re.IGNORECASE)
ETF_RE = re.compile(r"\betf\b", re.IGNORECASE)
