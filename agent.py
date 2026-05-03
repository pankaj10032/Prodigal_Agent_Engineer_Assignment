from __future__ import annotations

from decimal import Decimal
from typing import Optional

from llm_assistant import MultiAgentTurnAnalysis, OpenAILLMMultiAgent, TurnIntentHint
from models import ConversationState, ToolTrace
from parsers import parse_turn
from payment_api import AccountNotFoundError, ApiClientError, PaymentApiClient
from validators import (
    format_currency,
    has_two_or_fewer_decimals,
    is_valid_account_id,
    is_valid_card_number,
    is_valid_cvv,
    is_valid_date_of_birth,
    is_valid_expiry,
    is_valid_payment_amount,
    quantize_money,
)


MAX_VERIFICATION_FAILURES = 3
MAX_PAYMENT_FAILURES = 3


class Agent:
    def __init__(
        self,
        api_client: Optional[PaymentApiClient] = None,
        llm_assistant: Optional[OpenAILLMMultiAgent] = None,
    ) -> None:
        self.api_client = api_client or PaymentApiClient()
        self.llm_assistant = llm_assistant if llm_assistant is not None else OpenAILLMMultiAgent.from_env()
        self.state = ConversationState()

    def next(self, user_input: str) -> dict:
        self.state.begin_turn()

        if self.state.closed:
            return self._respond(
                "This payment session is closed. Please create a new agent session if you need more help."
            )

        text = (user_input or "").strip()
        parsed = self._parse_turn(text)
        llm_analysis = self._safe_llm_analysis(text)
        intent_hint = llm_analysis.intent_hint if llm_analysis else None

        control_message = self._apply_global_controls(parsed)
        if control_message:
            return self._respond(control_message)

        if not self.state.account:
            response = self._handle_account_lookup(
                text=text,
                parsed_account_id=parsed.account_id,
                intent_hint=intent_hint,
                llm_analysis=llm_analysis,
            )
            if response:
                return self._respond(response)
            parsed = self._parse_turn(text)

        if not self.state.verified:
            self._capture_verification_inputs(parsed)
            response = self._handle_verification(
                parsed=parsed,
                intent_hint=intent_hint,
                llm_analysis=llm_analysis,
            )
            if response:
                return self._respond(response)

        if self.state.closed:
            return self._respond(
                "This payment session is closed. Please create a new agent session if you need more help."
            )

        self._capture_payment_amount(parsed)
        response = self._handle_payment_flow(parsed, intent_hint=intent_hint, llm_analysis=llm_analysis)
        return self._respond(response)

    def _safe_llm_analysis(self, text: str) -> Optional[MultiAgentTurnAnalysis]:
        if not self.llm_assistant:
            return None
        try:
            return self.llm_assistant.analyze_turn(
                text=text,
                stage=self.state.stage,
                account_loaded=self.state.account is not None,
                verified=self.state.verified,
            )
        except Exception:
            return None

    def _parse_turn(self, text: str):
        return parse_turn(
            text,
            known_name=self.state.account.full_name if self.state.account else None,
            allow_bare_secondary_digits=self.state.account is not None and not self.state.verified,
            allow_bare_cvv=self.state.stage == "awaiting_cvv",
            allow_bare_expiry=self.state.stage == "awaiting_expiry",
        )

    def _apply_global_controls(self, parsed) -> Optional[str]:
        if parsed.cancel:
            self.state.stage = "cancelled"
            self.state.closed = True
            self.state.add_audit_event("session_cancelled", "User cancelled the session.")
            return (
                "This payment session has been cancelled. Please start a new session if you need more help."
            )

        if parsed.change_amount and self.state.verified:
            self.state.reset_payment_inputs()
            self.state.stage = "awaiting_amount"
            self.state.add_audit_event(
                "payment_amount_reset",
                "Payment amount and card details were cleared for re-entry.",
            )

        return None

    def _handle_account_lookup(
        self,
        *,
        text: str,
        parsed_account_id: Optional[str],
        intent_hint: Optional[TurnIntentHint],
        llm_analysis: Optional[MultiAgentTurnAnalysis],
    ) -> Optional[str]:
        if not parsed_account_id:
            self.state.stage = "awaiting_account_id"
            if not text:
                return "Hello! Please share your account ID to get started."
            if llm_analysis and llm_analysis.guidance_hint and llm_analysis.guidance_hint.message:
                if llm_analysis.guidance_hint.active_agent == "account_support":
                    return llm_analysis.guidance_hint.message
            if intent_hint and intent_hint.intent == "account_lookup_attempt":
                return "Please share a valid account ID in the format `ACC1001`."
            if "account" in text.lower():
                return "Please share a valid account ID in the format `ACC1001`."
            return "Hello! Please share your account ID to get started."

        if not is_valid_account_id(parsed_account_id):
            self.state.stage = "awaiting_account_id"
            return "Please share a valid account ID in the format `ACC1001`."

        try:
            account = self.api_client.lookup_account(parsed_account_id)
        except AccountNotFoundError:
            self.state.stage = "awaiting_account_id"
            self.state.add_tool_trace(
                ToolTrace(
                    tool="lookup_account",
                    status="not_found",
                    detail=f"Account `{parsed_account_id}` was not found.",
                    retriable=True,
                )
            )
            self.state.add_audit_event("account_lookup_failed", "Account lookup returned not found.")
            return "I couldn't find that account ID. Please check it and share a valid account ID."
        except ApiClientError:
            self.state.stage = "lookup_unavailable"
            self.state.closed = True
            self.state.add_tool_trace(
                ToolTrace(
                    tool="lookup_account",
                    status="error",
                    detail="Lookup service was unavailable.",
                    retriable=True,
                )
            )
            self.state.add_audit_event("account_lookup_failed", "Lookup service was unavailable.")
            return (
                "I couldn't reach the account lookup service right now. "
                "Please start a new session and try again later."
            )

        self.state.account_id = account.account_id
        self.state.account = account
        self.state.stage = "awaiting_full_name"
        self.state.add_tool_trace(
            ToolTrace(
                tool="lookup_account",
                status="success",
                detail=f"Loaded account `{account.account_id}`.",
            )
        )
        self.state.add_audit_event("account_lookup_succeeded", "Account context loaded.")
        return None

    def _capture_verification_inputs(self, parsed) -> None:
        if parsed.full_name and not self.state.provided_full_name:
            self.state.provided_full_name = parsed.full_name
        if parsed.dob and is_valid_date_of_birth(parsed.dob) and not self.state.provided_dob:
            self.state.provided_dob = parsed.dob
        if parsed.aadhaar_last4 and not self.state.provided_aadhaar_last4:
            self.state.provided_aadhaar_last4 = parsed.aadhaar_last4
        if parsed.pincode and not self.state.provided_pincode:
            self.state.provided_pincode = parsed.pincode

    def _handle_verification(
        self,
        *,
        parsed,
        intent_hint: Optional[TurnIntentHint],
        llm_analysis: Optional[MultiAgentTurnAnalysis],
    ) -> Optional[str]:
        assert self.state.account is not None

        if not self.state.provided_full_name:
            self.state.stage = "awaiting_full_name"
            if llm_analysis and llm_analysis.guidance_hint and llm_analysis.guidance_hint.message:
                if llm_analysis.guidance_hint.active_agent == "verification_support":
                    return llm_analysis.guidance_hint.message
            if intent_hint and intent_hint.intent == "verification_attempt":
                return "I still need your full name exactly as it appears on the account."
            return "Got it. Please confirm your full name exactly as it appears on the account."

        if parsed.invalid_dob_detected:
            self.state.stage = "awaiting_secondary_verification"
            return (
                "Please share a valid date of birth in `YYYY-MM-DD` format, "
                "or provide Aadhaar last 4 or pincode."
            )

        if not any(
            [
                self.state.provided_dob,
                self.state.provided_aadhaar_last4,
                self.state.provided_pincode,
            ]
        ):
            self.state.stage = "awaiting_secondary_verification"
            if llm_analysis and llm_analysis.guidance_hint and llm_analysis.guidance_hint.message:
                if llm_analysis.guidance_hint.active_agent == "verification_support":
                    return llm_analysis.guidance_hint.message
            if intent_hint and intent_hint.intent == "verification_attempt":
                return (
                    "I couldn't use that verification detail. Please verify your identity with one of these: "
                    "date of birth (`YYYY-MM-DD`), Aadhaar last 4, or pincode."
                )
            return (
                "Thanks. Please verify your identity with one of these: date of birth "
                "(`YYYY-MM-DD`), Aadhaar last 4, or pincode."
            )

        name_match = self.state.provided_full_name == self.state.account.full_name
        secondary_match = any(
            [
                self.state.provided_dob == self.state.account.dob if self.state.provided_dob else False,
                self.state.provided_aadhaar_last4 == self.state.account.aadhaar_last4
                if self.state.provided_aadhaar_last4
                else False,
                self.state.provided_pincode == self.state.account.pincode
                if self.state.provided_pincode
                else False,
            ]
        )

        if not (name_match and secondary_match):
            self.state.verification_failures += 1
            remaining = MAX_VERIFICATION_FAILURES - self.state.verification_failures
            self.state.reset_verification_inputs()
            self.state.add_audit_event("verification_failed", "Identity verification failed.")

            if remaining <= 0:
                self.state.stage = "verification_locked"
                self.state.closed = True
                self.state.add_audit_event("verification_locked", "Verification attempts exhausted.")
                return (
                    "I couldn't verify your identity after 3 attempts, so this session is now closed. "
                    "Please start a new session if you still need help."
                )

            self.state.stage = "awaiting_full_name"
            return (
                "That information did not match our records. "
                f"You have {remaining} verification attempt(s) remaining. "
                "Please re-enter your full name and one of: date of birth, Aadhaar last 4, or pincode."
            )

        self.state.verified = True
        self.state.stage = "awaiting_amount"
        self.state.add_audit_event("verification_succeeded", "Identity verified.")
        if self.state.account.balance <= Decimal("0"):
            self.state.closed = True
            self.state.add_audit_event("zero_balance_closed", "Verified account has no outstanding balance.")
            return (
                "Identity verified. Your outstanding balance is ₹0.00, so there is no payment due right now. "
                "This session is now closed."
            )
        self.state.balance_shared = True
        return None

    def _capture_payment_amount(self, parsed) -> None:
        if self.state.stage != "awaiting_amount":
            return
        if self.state.payment_amount is None and parsed.amount is not None:
            self.state.payment_amount_had_too_many_decimals = not has_two_or_fewer_decimals(parsed.amount)
            self.state.payment_amount = parsed.amount
            self.state.add_audit_event("payment_amount_captured", "Captured payment amount candidate.")

    def _capture_payment_method_inputs(self, parsed) -> None:
        if self.state.payment_amount is None:
            return

        if not self.state.card.cardholder_name:
            self.state.card.cardholder_name = parsed.cardholder_name or (
                parsed.full_name if self.state.verified else None
            )

        if not self.state.card.card_number and parsed.card_number:
            self.state.card.card_number = parsed.card_number

        if not self.state.card.cvv and parsed.cvv:
            self.state.card.cvv = parsed.cvv

        if self.state.card.expiry_month is None and parsed.expiry_month is not None:
            self.state.card.expiry_month = parsed.expiry_month

        if self.state.card.expiry_year is None and parsed.expiry_year is not None:
            self.state.card.expiry_year = parsed.expiry_year

    def _handle_payment_flow(
        self,
        parsed,
        intent_hint: Optional[TurnIntentHint],
        llm_analysis: Optional[MultiAgentTurnAnalysis],
    ) -> str:
        assert self.state.account is not None

        if self.state.payment_amount is None:
            self.state.stage = "awaiting_amount"
            if llm_analysis and llm_analysis.guidance_hint and llm_analysis.guidance_hint.message:
                if llm_analysis.guidance_hint.active_agent in {"payment_support", "recovery_support"}:
                    return llm_analysis.guidance_hint.message
            if intent_hint and intent_hint.intent == "payment_attempt":
                return (
                    f"Your outstanding balance is {format_currency(self.state.account.balance)}. "
                    "Please enter the exact numeric amount you would like to pay."
                )
            return (
                f"Your outstanding balance is {format_currency(self.state.account.balance)}. "
                "Please tell me the amount you would like to pay."
            )

        if self.state.payment_amount_had_too_many_decimals:
            self.state.payment_amount = None
            self.state.payment_amount_had_too_many_decimals = False
            self.state.stage = "awaiting_amount"
            return "Please enter a valid payment amount greater than 0 with up to 2 decimal places."

        valid_amount, amount_error = is_valid_payment_amount(
            self.state.payment_amount,
            self.state.account.balance,
        )
        if not valid_amount:
            self.state.payment_amount = None
            self.state.stage = "awaiting_amount"
            if amount_error == "insufficient_balance":
                return (
                    "The payment amount cannot exceed your outstanding balance of "
                    f"{format_currency(self.state.account.balance)}. Please enter a lower amount."
                )
            return "Please enter a valid payment amount greater than 0 with up to 2 decimal places."

        self._capture_payment_method_inputs(parsed)

        if not self.state.card.cardholder_name:
            self.state.stage = "awaiting_cardholder_name"
            return (
                f"Got it. I can process {format_currency(self.state.payment_amount)}. "
                "Please share the cardholder name exactly as it appears on the card."
            )

        if not self.state.card.card_number:
            self.state.stage = "awaiting_card_number"
            return "Please share your card number."

        if not is_valid_card_number(self.state.card.card_number):
            self.state.card.card_number = None
            self.state.stage = "awaiting_card_number"
            return "That card number looks invalid. Please enter a valid, unmasked card number."

        if not self.state.card.cvv:
            self.state.stage = "awaiting_cvv"
            return "Please share the card CVV."

        if not is_valid_cvv(self.state.card.cvv, self.state.card.card_number):
            self.state.card.cvv = None
            self.state.stage = "awaiting_cvv"
            return "That CVV looks invalid for this card. Please re-enter the CVV."

        if self.state.card.expiry_month is None or self.state.card.expiry_year is None:
            self.state.stage = "awaiting_expiry"
            return "Please share the card expiry in `MM/YYYY` format."

        if not is_valid_expiry(self.state.card.expiry_month, self.state.card.expiry_year):
            self.state.card.clear_expiry()
            self.state.stage = "awaiting_expiry"
            return (
                "That expiry date is invalid or the card has expired. "
                "Please enter a valid future expiry in `MM/YYYY` format."
            )

        return self._process_payment()

    def _process_payment(self) -> str:
        assert self.state.account is not None
        assert self.state.payment_amount is not None
        assert self.state.card.cardholder_name is not None
        assert self.state.card.card_number is not None
        assert self.state.card.cvv is not None
        assert self.state.card.expiry_month is not None
        assert self.state.card.expiry_year is not None

        try:
            result = self.api_client.process_payment(
                account_id=self.state.account.account_id,
                amount=self.state.payment_amount,
                cardholder_name=self.state.card.cardholder_name,
                card_number=self.state.card.card_number,
                cvv=self.state.card.cvv,
                expiry_month=self.state.card.expiry_month,
                expiry_year=self.state.card.expiry_year,
            )
        except ApiClientError:
            self.state.add_tool_trace(
                ToolTrace(
                    tool="process_payment",
                    status="error",
                    detail="Payment service was unavailable.",
                    retriable=True,
                )
            )
            self.state.add_audit_event("payment_failed", "Payment service was unavailable.")
            self.state.reset_payment_inputs()
            self.state.closed = True
            self.state.stage = "payment_unavailable"
            return (
                "I couldn't reach the payment service right now, so I wasn't able to process the payment. "
                "Please start a new session and try again later."
            )

        if result.success:
            transaction_id = result.transaction_id or "unknown"
            amount_text = format_currency(quantize_money(self.state.payment_amount))
            self.state.add_tool_trace(
                ToolTrace(
                    tool="process_payment",
                    status="success",
                    detail=f"Payment processed for {amount_text}.",
                )
            )
            self.state.add_audit_event("payment_succeeded", "Payment completed successfully.")
            self.state.card.clear_sensitive()
            self.state.reset_payment_inputs()
            self.state.closed = True
            self.state.stage = "completed"
            return (
                f"Payment successful. Transaction ID: `{transaction_id}`. "
                f"I processed {amount_text} for account `{self.state.account.account_id}`. "
                "This session is now complete."
            )

        self.state.payment_failures += 1
        self.state.add_tool_trace(
            ToolTrace(
                tool="process_payment",
                status="rejected",
                detail="Payment was rejected by the payment service.",
                retriable=True,
                error_code=result.error_code,
            )
        )
        self.state.add_audit_event("payment_rejected", "Payment API rejected the current attempt.")
        if self.state.payment_failures >= MAX_PAYMENT_FAILURES:
            self.state.reset_payment_inputs()
            self.state.closed = True
            self.state.stage = "payment_locked"
            return (
                "I couldn't complete the payment after multiple attempts, so this session is now closed. "
                "Please start a new session if you want to try again."
            )

        return self._handle_payment_api_error(result.error_code)

    def _handle_payment_api_error(self, error_code: Optional[str]) -> str:
        if error_code == "invalid_amount":
            self.state.payment_amount = None
            self.state.stage = "awaiting_amount"
            return "The payment amount was rejected. Please enter a valid amount up to your outstanding balance."

        if error_code == "insufficient_balance":
            self.state.payment_amount = None
            self.state.stage = "awaiting_amount"
            return (
                f"That amount exceeds your outstanding balance of {format_currency(self.state.account.balance)}. "
                "Please enter a lower amount."
            )

        if error_code == "invalid_card":
            self.state.card.card_number = None
            self.state.card.cvv = None
            self.state.stage = "awaiting_card_number"
            return "The card was rejected by the payment service. Please enter a different valid card number."

        if error_code == "invalid_cvv":
            self.state.card.cvv = None
            self.state.stage = "awaiting_cvv"
            return "The CVV was rejected by the payment service. Please re-enter the CVV."

        if error_code == "invalid_expiry":
            self.state.card.clear_expiry()
            self.state.stage = "awaiting_expiry"
            return (
                "The expiry date was rejected by the payment service. "
                "Please enter a valid future expiry in `MM/YYYY` format."
            )

        self.state.stage = "awaiting_card_number"
        self.state.card.card_number = None
        self.state.card.cvv = None
        return "The payment could not be completed. Please re-enter your card details and try again."

    def _respond(self, message: str) -> dict:
        return {"message": message}
