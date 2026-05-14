from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    full_name: str
    dob: str
    aadhaar_last4: str
    pincode: str
    balance: Decimal


@dataclass
class CardDetails:
    cardholder_name: Optional[str] = None
    card_number: Optional[str] = None
    cvv: Optional[str] = None
    expiry_month: Optional[int] = None
    expiry_year: Optional[int] = None

    def clear_sensitive(self) -> None:
        self.card_number = None
        self.cvv = None

    def clear_expiry(self) -> None:
        self.expiry_month = None
        self.expiry_year = None

    def clear_all(self) -> None:
        self.cardholder_name = None
        self.card_number = None
        self.cvv = None
        self.expiry_month = None
        self.expiry_year = None


@dataclass
class PaymentResult:
    success: bool
    transaction_id: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None


@dataclass
class ToolTrace:
    tool: str
    status: str
    detail: str
    retriable: bool = False
    error_code: Optional[str] = None

    def as_dict(self) -> dict:
        payload = {
            "tool": self.tool,
            "status": self.status,
            "detail": self.detail,
            "retriable": self.retriable,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


@dataclass
class AuditEvent:
    event_type: str
    detail: str
    stage: str

    def as_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "detail": self.detail,
            "stage": self.stage,
        }


@dataclass
class ConversationState:
    stage: str = "awaiting_account_id"
    account_id: Optional[str] = None
    account: Optional[AccountSnapshot] = None
    provided_full_name: Optional[str] = None
    provided_dob: Optional[str] = None
    provided_aadhaar_last4: Optional[str] = None
    provided_pincode: Optional[str] = None
    verification_failures: int = 0
    verified: bool = False
    balance_shared: bool = False
    payment_amount: Optional[Decimal] = None
    payment_amount_had_too_many_decimals: bool = False
    card: CardDetails = field(default_factory=CardDetails)
    payment_failures: int = 0
    current_field_retries: int = 0 # Retries for the current requested field
    closed: bool = False
    history: list[dict[str, str]] = field(default_factory=list)
    memory: dict[str, Any] = field(default_factory=dict)
    last_tool_traces: list[ToolTrace] = field(default_factory=list)
    audit_log: list[AuditEvent] = field(default_factory=list)

    def reset_verification_inputs(self) -> None:
        self.provided_full_name = None
        self.provided_dob = None
        self.provided_aadhaar_last4 = None
        self.provided_pincode = None

    def reset_payment_inputs(self) -> None:
        self.payment_amount = None
        self.payment_amount_had_too_many_decimals = False
        self.card = CardDetails()

    def begin_turn(self) -> None:
        self.last_tool_traces = []

    def add_tool_trace(self, trace: ToolTrace) -> None:
        self.last_tool_traces.append(trace)

    def add_audit_event(self, event_type: str, detail: str) -> None:
        self.audit_log.append(AuditEvent(event_type=event_type, detail=detail, stage=self.stage))
        if len(self.audit_log) > 30:
            self.audit_log = self.audit_log[-30:]
