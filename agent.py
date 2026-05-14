from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any

from llm_assistant import OpenAILLMMultiAgent
from models import ConversationState, ToolTrace, AccountSnapshot
from payment_api import AccountNotFoundError, ApiClientError, PaymentApiClient
from validators import (
    format_currency,
    is_valid_account_id,
    is_valid_card_number,
    is_valid_cvv,
    is_valid_date_of_birth,
    is_valid_expiry,
    quantize_money,
)
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("PaymentAgent")


MAX_VERIFICATION_FAILURES = 3
MAX_PAYMENT_FAILURES = 3


class Agent:
    def __init__(
        self,
        api_client: Optional[PaymentApiClient] = None,
        llm_assistant: Optional[OpenAILLMMultiAgent] = None,
    ) -> None:
        self.api_client = api_client or PaymentApiClient()
        self.llm_assistant = llm_assistant or OpenAILLMMultiAgent.from_env() or OpenAILLMMultiAgent()
        self.state = ConversationState()
        logger.info("Agent initialized.")

    def next(self, user_input: str) -> dict:
        self.state.begin_turn()

        if self.state.closed:
            return {"message": "This session is closed. Please start a new one if you need more help."}

        # 1. Update history with user input
        logger.info(f"Received user input: {user_input}")
        self.state.history.append({"role": "user", "content": user_input})

        # 2. Extract information using LLM
        extraction = {}
        if self.llm_assistant and self.llm_assistant.api_key:
            logger.info("Calling LLM for information extraction...")
            current_state_summary = {
                "stage": self.state.stage,
                "account_id": self.state.account_id,
                "verified": self.state.verified,
                "has_amount": self.state.payment_amount is not None,
                "card_fields_present": [
                    k for k, v in self.state.card.__dict__.items() if v is not None
                ]
            }
            extraction = self.llm_assistant.extract_information(
                user_input, self.state.history, self.state.memory, current_state_summary
            )
            logger.info(f"Extracted info: {extraction}")
        else:
            logger.warning("LLM Assistant unavailable or API key missing.")

        # 3. Update state and memory from extraction
        old_stage = self.state.stage
        old_memory_count = len([v for v in self.state.memory.values() if v is not None])
        
        self._update_state_from_extraction(extraction)
        
        new_memory_count = len([v for v in self.state.memory.values() if v is not None])
        
        # 4. Check for progress and track retries
        if not self.state.closed:
            # If stage didn't change and no new memory was added, it's a failed attempt to provide info
            if self.state.stage == old_stage and new_memory_count <= old_memory_count:
                # But don't count if it's the very first message (greeting)
                if len(self.state.history) > 1:
                    self.state.current_field_retries += 1
                    logger.info(f"Field retry count: {self.state.current_field_retries}")
            else:
                # Progress made!
                self.state.current_field_retries = 0
                logger.info("Progress made, resetting field retry count.")

            if self.state.current_field_retries >= 3:
                self.state.closed = True
                logger.warning("Max field retries reached. Closing session.")
                # We'll let run_logic handle the final message or just return here

        # 4. Deterministic Logic & Tool Calling
        outcome = self._run_logic()
        if outcome:
            logger.info(f"Logic outcome: {outcome}")

        # 5. Generate Response using LLM
        if self.llm_assistant and self.llm_assistant.api_key:
            logger.info("Calling LLM for response generation...")
            response_message = self.llm_assistant.generate_response(
                self.state.history, self._get_serializable_state(), last_outcome=outcome
            )
        else:
            # Fallback to simple response if LLM is unavailable
            logger.warning("LLM Assistant unavailable for response. Using fallback.")
            response_message = f"I received your message: \"{user_input}\". (LLM Assistant Unavailable - please check your API key)"

        # 6. Update history with agent response
        self.state.history.append({"role": "assistant", "content": response_message})

        return {"message": response_message}

    def _update_state_from_extraction(self, extraction: Dict[str, Any]) -> None:
        """
        Updates internal state and memory based on LLM extraction.
        """
        if extraction.get("intent") == "cancel":
            self.state.stage = "cancelled"
            self.state.closed = True
            logger.info("User requested cancellation.")
            return

        # Update persistent memory
        new_memory = extraction.get("memory", {})
        if new_memory:
            self.state.memory.update({k: v for k, v in new_memory.items() if v is not None})
            # Handle explicit nulls (deletions)
            for k, v in new_memory.items():
                if v is None and k in self.state.memory:
                    self.state.memory[k] = None

        # Sync memory to specific state fields
        mem = self.state.memory
        
        # Account ID
        if mem.get("account_id") and not self.state.account:
            self.state.account_id = str(mem["account_id"]).strip().upper()
        elif mem.get("account_id") is None:
            self.state.account_id = None
            self.state.account = None

        # Verification fields (allow overwriting if not yet verified)
        if not self.state.verified:
            self.state.provided_full_name = mem.get("full_name")
            
            dob = mem.get("dob")
            if dob and is_valid_date_of_birth(str(dob)):
                self.state.provided_dob = str(dob)
            else:
                self.state.provided_dob = None
                
            self.state.provided_aadhaar_last4 = str(mem.get("aadhaar_last4")) if mem.get("aadhaar_last4") else None
            self.state.provided_pincode = str(mem.get("pincode")) if mem.get("pincode") else None

        # Payment Amount
        if mem.get("payment_amount") is not None:
            try:
                self.state.payment_amount = Decimal(str(mem["payment_amount"]))
            except (InvalidOperation, ValueError):
                pass
        else:
            self.state.payment_amount = None

        # Card details
        self.state.card.cardholder_name = mem.get("cardholder_name")
        if mem.get("card_number"):
            self.state.card.card_number = re.sub(r"[\s-]", "", str(mem["card_number"]))
        else:
            self.state.card.card_number = None
            
        self.state.card.cvv = str(mem.get("cvv")) if mem.get("cvv") else None
        
        try:
            self.state.card.expiry_month = int(mem["expiry_month"]) if mem.get("expiry_month") else None
        except (ValueError, TypeError):
            self.state.card.expiry_month = None
            
        try:
            if mem.get("expiry_year"):
                year = int(mem["expiry_year"])
                if year < 100: year += 2000
                self.state.card.expiry_year = year
            else:
                self.state.card.expiry_year = None
        except (ValueError, TypeError):
            self.state.card.expiry_year = None

    def _run_logic(self) -> Optional[str]:
        if self.state.closed and self.state.current_field_retries >= 3:
            return "I've asked for this information multiple times but haven't received a valid response. For security, I'm closing this session. Please start over."

        if self.state.stage == "cancelled":
            return "User cancelled the session. Recap and close."

        # Account Lookup
        if self.state.account_id and not self.state.account:
            try:
                account = self.api_client.lookup_account(self.state.account_id)
                self.state.account = account
                self.state.stage = "awaiting_verification"
                self.state.add_audit_event("account_lookup_succeeded", f"Loaded account {account.account_id}")
                return f"Account {account.account_id} found. Need verification."
            except AccountNotFoundError:
                self.state.account_id = None # Reset to let user try again
                return "Account not found. Please provide a valid Account ID."
            except ApiClientError:
                self.state.closed = True
                return "Account service unavailable. Session closed."

        # Verification
        if self.state.account and not self.state.verified:
            # Check if we have Name + (DOB or Aadhaar or Pincode)
            has_name = self.state.provided_full_name is not None
            has_secondary = any([
                self.state.provided_dob,
                self.state.provided_aadhaar_last4,
                self.state.provided_pincode
            ])

            if has_name and has_secondary:
                # STRICT MATCHING
                name_match = self.state.provided_full_name == self.state.account.full_name
                secondary_match = False
                if self.state.provided_dob == self.state.account.dob:
                    secondary_match = True
                elif self.state.provided_aadhaar_last4 == self.state.account.aadhaar_last4:
                    secondary_match = True
                elif self.state.provided_pincode == self.state.account.pincode:
                    secondary_match = True

                if name_match and secondary_match:
                    self.state.verified = True
                    self.state.stage = "verified"
                    self.state.add_audit_event("verification_succeeded", "Identity verified.")
                    return f"Verification successful. Balance is {format_currency(self.state.account.balance)}."
                else:
                    self.state.verification_failures += 1
                    self.state.reset_verification_inputs()
                    if self.state.verification_failures >= MAX_VERIFICATION_FAILURES:
                        self.state.closed = True
                        return "Verification failed 3 times. Session locked."
                    return f"Verification details do not match. Attempt {self.state.verification_failures}/3."
            else:
                return "Waiting for full name and at least one secondary factor (DOB, Aadhaar last 4, or Pincode)."

        # Payment Processing
        if self.state.verified and not self.state.closed:
            # Check if all payment info is present
            if all([
                self.state.payment_amount,
                self.state.card.cardholder_name,
                self.state.card.card_number,
                self.state.card.cvv,
                self.state.card.expiry_month,
                self.state.card.expiry_year
            ]):
                # Validate amount
                if self.state.payment_amount > self.state.account.balance:
                    self.state.payment_amount = None
                    return f"Amount exceeds balance of {format_currency(self.state.account.balance)}."

                # Validate card details
                if not is_valid_card_number(self.state.card.card_number):
                    self.state.card.card_number = None
                    return "Invalid card number."
                if not is_valid_cvv(self.state.card.cvv, self.state.card.card_number):
                    self.state.card.cvv = None
                    return "Invalid CVV."
                if not is_valid_expiry(self.state.card.expiry_month, self.state.card.expiry_year):
                    self.state.card.clear_expiry()
                    return "Invalid or expired card."

                # Process Payment
                try:
                    result = self.api_client.process_payment(
                        account_id=self.state.account.account_id,
                        amount=self.state.payment_amount,
                        cardholder_name=self.state.card.cardholder_name,
                        card_number=self.state.card.card_number,
                        cvv=self.state.card.cvv,
                        expiry_month=self.state.card.expiry_month,
                        expiry_year=self.state.card.expiry_year
                    )
                    if result.success:
                        self.state.closed = True
                        self.state.stage = "completed"
                        self.state.card.clear_all() # Security: Clear sensitive data
                        return f"Payment successful. Transaction ID: {result.transaction_id}."
                    else:
                        self.state.payment_failures += 1
                        if self.state.payment_failures >= MAX_PAYMENT_FAILURES:
                            self.state.closed = True
                            self.state.card.clear_all() # Security: Clear sensitive data
                            return f"Payment failed: {result.message}. Attempts exhausted."
                        return f"Payment failed: {result.message}."
                except ApiClientError:
                    self.state.closed = True
                    return "Payment service unavailable. Session closed."
            else:
                return "Collecting payment details (amount, cardholder name, number, cvv, expiry)."

        return None

    def _get_serializable_state(self) -> Dict[str, Any]:
        return {
            "stage": self.state.stage,
            "account_id": self.state.account_id,
            "balance": str(self.state.account.balance) if self.state.account else None,
            "verified": self.state.verified,
            "verification_failures": self.state.verification_failures,
            "payment_amount": str(self.state.payment_amount) if self.state.payment_amount else None,
            "card_captured": {
                "name": self.state.card.cardholder_name is not None,
                "number": self.state.card.card_number is not None,
                "cvv": self.state.card.cvv is not None,
                "expiry": self.state.card.expiry_month is not None
            },
            "closed": self.state.closed
        }
import re # Added missing import
