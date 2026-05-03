from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Optional

from validators import ACCOUNT_ID_RE, DATE_RE, clean_card_number, is_valid_date_of_birth, parse_amount


NAME_PATTERN = re.compile(
    r"(?:my name is|name is|i am|i'm|this is)\s+([A-Za-z][A-Za-z .'-]{1,79})",
    re.IGNORECASE,
)
CARDHOLDER_PATTERN = re.compile(
    r"(?:cardholder name is|cardholder is|name on card is|name on the card is)\s+([A-Za-z][A-Za-z .'-]{1,79})",
    re.IGNORECASE,
)
AADHAAR_PATTERN = re.compile(
    r"(?:aadhaar(?:\s+last\s*4)?|aadhar(?:\s+last\s*4)?)(?:\s+is|:)?\s*(\d{4})",
    re.IGNORECASE,
)
PINCODE_PATTERN = re.compile(
    r"(?:pincode|pin code|postal code)(?:\s+is|:)?\s*(\d{6})",
    re.IGNORECASE,
)
AMOUNT_PATTERN = re.compile(
    r"(?:₹\s*([0-9]+(?:\.[0-9]{1,3})?)|(?:pay|amount|payment)(?:\s+is|\s+to|:)?\s*₹?\s*([0-9]+(?:\.[0-9]{1,3})?))",
    re.IGNORECASE,
)
CVV_PATTERN = re.compile(r"(?:cvv|cvc)(?:\s+is|:)?\s*(\d{3,4})", re.IGNORECASE)
EXPIRY_PATTERN = re.compile(
    r"(?:expiry|exp|expires|valid thru|valid through)(?:\s+is|:)?\s*(0?[1-9]|1[0-2])\s*/\s*(\d{2,4})",
    re.IGNORECASE,
)
EXPIRY_MONTH_PATTERN = re.compile(
    r"(?:expiry month|exp month|month)(?:\s+is|:)?\s*(0?[1-9]|1[0-2])",
    re.IGNORECASE,
)
EXPIRY_YEAR_PATTERN = re.compile(
    r"(?:expiry year|exp year|year)(?:\s+is|:)?\s*(\d{2,4})",
    re.IGNORECASE,
)
CARD_NUMBER_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")
CONFIRM_PATTERN = re.compile(r"\b(confirm|proceed|charge it|go ahead)\b", re.IGNORECASE)
CANCEL_PATTERN = re.compile(r"\b(cancel|stop|abort|never mind|nevermind)\b", re.IGNORECASE)
CHANGE_AMOUNT_PATTERN = re.compile(
    r"\b(change (the )?amount|different amount|edit amount)\b",
    re.IGNORECASE,
)


@dataclass
class ParsedTurn:
    account_id: Optional[str] = None
    full_name: Optional[str] = None
    dob: Optional[str] = None
    invalid_dob_detected: bool = False
    aadhaar_last4: Optional[str] = None
    pincode: Optional[str] = None
    amount: Optional[Decimal] = None
    cardholder_name: Optional[str] = None
    card_number: Optional[str] = None
    cvv: Optional[str] = None
    expiry_month: Optional[int] = None
    expiry_year: Optional[int] = None
    confirm_payment: bool = False
    cancel: bool = False
    change_amount: bool = False


def _normalize_year(year_text: str) -> int:
    if len(year_text) == 2:
        return 2000 + int(year_text)
    return int(year_text)


def extract_account_id(text: str) -> Optional[str]:
    match = ACCOUNT_ID_RE.search(text)
    return match.group(0).upper() if match else None


def extract_full_name(text: str, known_name: Optional[str] = None) -> Optional[str]:
    if known_name and known_name in text:
        return known_name
    match = NAME_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    stripped = text.strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,79}", stripped):
        return stripped
    generic_matches = re.findall(r"([A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+)+)", text)
    blocked_tokens = {
        "account",
        "id",
        "dob",
        "date",
        "birth",
        "aadhaar",
        "aadhar",
        "pincode",
        "pin",
        "code",
        "cardholder",
        "card",
        "expiry",
        "cvv",
        "payment",
        "amount",
        "pay",
        "valid",
        "through",
        "thru",
    }
    for candidate in generic_matches:
        normalized = candidate.strip().lower()
        candidate_tokens = set(normalized.replace(".", " ").split())
        if not candidate_tokens.intersection(blocked_tokens):
            return candidate.strip()
    return None


def extract_cardholder_name(text: str) -> Optional[str]:
    match = CARDHOLDER_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return None


def extract_dob(text: str) -> tuple[Optional[str], bool]:
    match = DATE_RE.search(text)
    if not match:
        return None, False
    candidate = match.group(0)
    if is_valid_date_of_birth(candidate):
        return candidate, False
    return None, True


def extract_aadhaar_last4(text: str, allow_bare_digits: bool = False) -> Optional[str]:
    match = AADHAAR_PATTERN.search(text)
    if match:
        return match.group(1)
    if allow_bare_digits:
        bare_match = re.search(r"(?<![A-Za-z0-9])(\d{4})(?!\d)", text)
        if bare_match:
            return bare_match.group(1)
    return None


def extract_pincode(text: str, allow_bare_digits: bool = False) -> Optional[str]:
    match = PINCODE_PATTERN.search(text)
    if match:
        return match.group(1)
    if allow_bare_digits:
        bare_match = re.search(r"(?<![A-Za-z0-9])(\d{6})(?!\d)", text)
        if bare_match:
            return bare_match.group(1)
    return None


def extract_amount(text: str) -> Optional[Decimal]:
    match = AMOUNT_PATTERN.search(text)
    if match:
        value = match.group(1) or match.group(2)
        return parse_amount(value)
    stripped = text.strip().replace(",", "")
    if re.fullmatch(r"₹?\d+(?:\.\d{1,3})?", stripped):
        return parse_amount(stripped)
    return None


def extract_card_number(text: str) -> Optional[str]:
    match = CARD_NUMBER_PATTERN.search(text)
    if not match:
        return None
    return clean_card_number(match.group(0))


def extract_cvv(text: str, allow_bare_digits: bool = False) -> Optional[str]:
    match = CVV_PATTERN.search(text)
    if match:
        return match.group(1)
    stripped = text.strip()
    if allow_bare_digits and re.fullmatch(r"\d{3,4}", stripped):
        return stripped
    return None


def extract_expiry(text: str, allow_bare: bool = False) -> tuple[Optional[int], Optional[int]]:
    match = EXPIRY_PATTERN.search(text)
    if match:
        return int(match.group(1)), _normalize_year(match.group(2))

    stripped = text.strip()
    if allow_bare:
        bare_match = re.fullmatch(r"(0?[1-9]|1[0-2])\s*/\s*(\d{2,4})", stripped)
        if bare_match:
            return int(bare_match.group(1)), _normalize_year(bare_match.group(2))

    month_match = EXPIRY_MONTH_PATTERN.search(text)
    year_match = EXPIRY_YEAR_PATTERN.search(text)
    month = int(month_match.group(1)) if month_match else None
    year = _normalize_year(year_match.group(1)) if year_match else None
    return month, year


def parse_turn(
    text: str,
    *,
    known_name: Optional[str] = None,
    allow_bare_secondary_digits: bool = False,
    allow_bare_cvv: bool = False,
    allow_bare_expiry: bool = False,
) -> ParsedTurn:
    dob, dob_found = extract_dob(text)
    expiry_month, expiry_year = extract_expiry(text, allow_bare=allow_bare_expiry)
    return ParsedTurn(
        account_id=extract_account_id(text),
        full_name=extract_full_name(text, known_name=known_name),
        dob=dob,
        invalid_dob_detected=dob_found and dob is None,
        aadhaar_last4=extract_aadhaar_last4(text, allow_bare_digits=allow_bare_secondary_digits),
        pincode=extract_pincode(text, allow_bare_digits=allow_bare_secondary_digits),
        amount=extract_amount(text),
        cardholder_name=extract_cardholder_name(text),
        card_number=extract_card_number(text),
        cvv=extract_cvv(text, allow_bare_digits=allow_bare_cvv),
        expiry_month=expiry_month,
        expiry_year=expiry_year,
        confirm_payment=bool(CONFIRM_PATTERN.search(text)),
        cancel=bool(CANCEL_PATTERN.search(text)),
        change_amount=bool(CHANGE_AMOUNT_PATTERN.search(text)),
    )
