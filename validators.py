from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Optional


ACCOUNT_ID_RE = re.compile(r"\bACC\d{4,}\b", re.IGNORECASE)
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def normalize_account_id(raw: str) -> str:
    return raw.strip().upper()


def is_valid_account_id(account_id: str) -> bool:
    return bool(ACCOUNT_ID_RE.fullmatch(account_id.strip()))


def is_valid_date_of_birth(value: str) -> bool:
    if not DATE_RE.fullmatch(value.strip()):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_valid_aadhaar_last4(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", value.strip()))


def is_valid_pincode(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", value.strip()))


def parse_amount(value: str) -> Optional[Decimal]:
    cleaned = value.strip().replace(",", "")
    if cleaned.startswith("₹"):
        cleaned = cleaned[1:]
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def has_two_or_fewer_decimals(amount: Decimal) -> bool:
    exponent = amount.as_tuple().exponent
    return exponent >= -2


def is_valid_payment_amount(amount: Decimal, balance: Decimal) -> tuple[bool, Optional[str]]:
    if amount <= Decimal("0"):
        return False, "invalid_amount"
    if not has_two_or_fewer_decimals(amount):
        return False, "invalid_amount"
    if amount > balance:
        return False, "insufficient_balance"
    return True, None


def quantize_money(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_currency(amount: Decimal) -> str:
    return f"₹{quantize_money(amount):,.2f}"


def clean_card_number(raw: str) -> str:
    return re.sub(r"[\s-]", "", raw)


def is_amex(card_number: str) -> bool:
    digits = clean_card_number(card_number)
    return len(digits) == 15 and digits.startswith(("34", "37"))


def passes_luhn(card_number: str) -> bool:
    digits = clean_card_number(card_number)
    if not digits.isdigit():
        return False

    total = 0
    reverse_digits = digits[::-1]
    for idx, char in enumerate(reverse_digits):
        digit = int(char)
        if idx % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def is_valid_card_number(card_number: str) -> bool:
    digits = clean_card_number(card_number)
    if not digits.isdigit():
        return False
    if "*" in card_number or "x" in card_number.lower():
        return False
    if len(digits) < 13 or len(digits) > 19:
        return False
    return passes_luhn(digits)


def is_valid_cvv(cvv: str, card_number: Optional[str]) -> bool:
    if not re.fullmatch(r"\d{3,4}", cvv.strip()):
        return False
    if card_number and is_amex(card_number):
        return len(cvv.strip()) == 4
    return len(cvv.strip()) == 3


def is_valid_expiry(month: int, year: int, today: Optional[date] = None) -> bool:
    if month < 1 or month > 12:
        return False
    today = today or date.today()
    if year < today.year:
        return False
    if year == today.year and month < today.month:
        return False
    return True
