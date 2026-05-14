import json
import os
import re
from typing import Optional, List, Dict, Any

import requests


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o"  # Capable model for extraction and conversational response


class LlmAssistantError(Exception):
    pass


class OpenAILLMMultiAgent:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: int = 30,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> Optional["OpenAILLMMultiAgent"]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        return cls(api_key=api_key)

    def extract_information(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        memory: Dict[str, Any],
        current_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extracts structured information and updates memory from messy user input.
        """
        system_prompt = (
            "You are an expert data extraction agent for a debt collection and payment system. "
            "Your goal is to maintain a 'memory' of user-provided details and extract new information. "
            "MEMORY FIELDS:\n"
            "- account_id, full_name, dob, aadhaar_last4, pincode, payment_amount, "
            "cardholder_name, card_number, cvv, expiry_month, expiry_year.\n"
            "(Note: phone number and email are NOT used and should not be extracted)\n"
            "\n"
            "RULES for MEMORY UPDATES:\n"
            "1. If the user provides NEW info, add it to memory.\n"
            "2. If the user wants to CHANGE or CORRECT info (e.g., 'no, my name is...', 'I want to change the name'), update that field in memory.\n"
            "3. If the user wants to DELETE or CLEAR info, set that field to null in memory.\n"
            "4. Detect the 'intent' (e.g., 'cancel').\n"
            "\n"
            "Return a JSON object with two keys:\n"
            "- 'memory': The COMPLETE updated memory dictionary (all fields included, null if missing).\n"
            "- 'intent': 'cancel' or null.\n"
            "\n"
            "Be extremely robust to messy, conversational formatting. Use history for context."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            *history[-10:],
            {"role": "user", "content": f"Current Memory: {json.dumps(memory)}\nInternal State: {json.dumps(current_state)}\nLatest User Input: {user_input}"}
        ]

        response = self._chat_completion(messages, response_format={"type": "json_object"})
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {}

    def generate_response(
        self,
        history: List[Dict[str, str]],
        current_state: Dict[str, Any],
        last_outcome: Optional[str] = None
    ) -> str:
        """
        Generates a natural, conversational response based on the full context and state.
        """
        system_prompt = (
            "You are a professional and helpful debt collection agent named 'Prodigal Assistant'. "
            "Your goal is to guide the user through: 1. Account Lookup, 2. Identity Verification, 3. Balance Review, 4. Payment. "
            "RULES:\n"
            "- Be conversational and empathetic, but clear.\n"
            "- If verification fails, explain clearly how many attempts are left (max 3).\n"
            "- If a payment is successful, provide the transaction ID and a recap.\n"
            "- NEVER reveal sensitive data like DOB, Aadhaar, or Pincode from the account records back to the user.\n"
            "- VALID SECONDARY VERIFICATION FACTORS: Date of Birth, Last 4 digits of Aadhaar, or Pincode. Do NOT ask for phone numbers or email addresses.\n"
            "- STRICTURE FOR RETRIES: If 'current_field_retries' is > 0, be more direct and strictly ask for the missing information. If it reaches 3, the session will close.\n"
            "- Only ask for one or two pieces of information at a time to avoid overwhelming the user.\n"
            "- Handle messy inputs gracefully and acknowledge what you've received.\n"
            "- If the user wants to cancel or exit, acknowledge and close the session.\n"
            "- If the session is closed/locked, explain why and how to start over."
        )

        user_content = f"Current Internal State: {json.dumps(current_state)}\n"
        if last_outcome:
            user_content += f"Outcome of last operation: {last_outcome}\n"
        
        messages = [
            {"role": "system", "content": system_prompt},
            *history[-10:],
            {"role": "user", "content": user_content}
        ]

        return self._chat_completion(messages)

    def _chat_completion(
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, str]] = None
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
        }
        if response_format:
            payload["response_format"] = response_format

        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            return body["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise LlmAssistantError(f"LLM request failed: {str(exc)}") from exc
