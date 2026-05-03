from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Optional

import requests


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


@dataclass(frozen=True)
class TurnIntentHint:
    intent: str
    confidence: str
    suggested_prompt: Optional[str] = None


@dataclass(frozen=True)
class TurnGuidanceHint:
    active_agent: str
    message: Optional[str]
    confidence: str


@dataclass(frozen=True)
class MultiAgentTurnAnalysis:
    intent_hint: TurnIntentHint
    guidance_hint: Optional[TurnGuidanceHint] = None


class LlmAssistantError(Exception):
    pass


class OpenAILLMMultiAgent:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: int = 15,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> Optional["OpenAILLMMultiAgent"]:
        enabled = os.getenv("ENABLE_LLM_ASSIST", "").strip().lower()
        api_key = os.getenv("OPENAI_API_KEY")
        if enabled not in {"1", "true", "yes"}:
            return None
        if not api_key:
            return None
        return cls(api_key=api_key)

    def analyze_turn(
        self,
        *,
        text: str,
        stage: str,
        account_loaded: bool,
        verified: bool,
    ) -> Optional[MultiAgentTurnAnalysis]:
        redacted_text = self._redact(text)
        if not redacted_text:
            return None

        triage_payload = self._request_structured_json(
            system_prompt=(
                "You are the triage agent in a payment-collection multi-agent system. "
                "Classify the user's redacted message and choose which specialist should help next. "
                "Never infer hidden sensitive values. Return strict JSON only."
            ),
            user_prompt=(
                f"stage={stage}\n"
                f"account_loaded={str(account_loaded).lower()}\n"
                f"verified={str(verified).lower()}\n"
                f"redacted_user_text={redacted_text}"
            ),
            format_name="triage_agent_output",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": [
                            "unknown",
                            "greeting",
                            "account_lookup_attempt",
                            "verification_attempt",
                            "payment_attempt",
                            "card_details_attempt",
                            "help_request",
                            "cancel",
                        ],
                    },
                    "route": {
                        "type": "string",
                        "enum": [
                            "none",
                            "account_support",
                            "verification_support",
                            "payment_support",
                            "recovery_support",
                        ],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "suggested_prompt": {
                        "type": ["string", "null"],
                    },
                },
                "required": ["intent", "route", "confidence", "suggested_prompt"],
            },
        )
        if not triage_payload:
            return None

        intent_hint = TurnIntentHint(
            intent=triage_payload.get("intent", "unknown"),
            confidence=triage_payload.get("confidence", "low"),
            suggested_prompt=triage_payload.get("suggested_prompt"),
        )

        route = triage_payload.get("route", "none")
        guidance_hint = self._specialist_guidance(
            route=route,
            redacted_text=redacted_text,
            stage=stage,
            account_loaded=account_loaded,
            verified=verified,
        )

        return MultiAgentTurnAnalysis(intent_hint=intent_hint, guidance_hint=guidance_hint)

    def _specialist_guidance(
        self,
        *,
        route: str,
        redacted_text: str,
        stage: str,
        account_loaded: bool,
        verified: bool,
    ) -> Optional[TurnGuidanceHint]:
        prompts = {
            "account_support": (
                "You are the account lookup support agent. "
                "Write one short, actionable sentence asking for the account ID format only."
            ),
            "verification_support": (
                "You are the verification coach agent. "
                "Write one short, policy-safe sentence about what verification detail is still needed. "
                "Never reveal any stored identity data."
            ),
            "payment_support": (
                "You are the payment collection agent. "
                "Write one short, actionable sentence about the next payment detail needed. "
                "Do not mention any sensitive values that were redacted."
            ),
            "recovery_support": (
                "You are the recovery agent. "
                "Write one short, actionable sentence to recover from a failed or unclear turn."
            ),
        }
        system_prompt = prompts.get(route)
        if not system_prompt:
            return None

        payload = self._request_structured_json(
            system_prompt=system_prompt,
            user_prompt=(
                f"stage={stage}\n"
                f"account_loaded={str(account_loaded).lower()}\n"
                f"verified={str(verified).lower()}\n"
                f"redacted_user_text={redacted_text}"
            ),
            format_name=f"{route}_output",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "guidance_message": {
                        "type": ["string", "null"],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                },
                "required": ["guidance_message", "confidence"],
            },
        )
        if not payload:
            return None

        return TurnGuidanceHint(
            active_agent=route,
            message=payload.get("guidance_message"),
            confidence=payload.get("confidence", "low"),
        )

    def _request_structured_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        format_name: str,
        schema: dict,
    ) -> Optional[dict]:
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": format_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }

        try:
            response = self.session.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise LlmAssistantError("LLM multi-agent request failed.") from exc

        return self._extract_text_json(body)

    def _extract_text_json(self, body: dict) -> Optional[dict]:
        for item in body.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    try:
                        return json.loads(content.get("text", ""))
                    except (TypeError, json.JSONDecodeError):
                        return None
                if content.get("type") == "refusal":
                    return None
        return None

    def _redact(self, text: str) -> str:
        redacted = text
        redacted = re.sub(r"\bACC\d{4,}\b", "[ACCOUNT_ID]", redacted, flags=re.IGNORECASE)
        redacted = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "[DATE]", redacted)
        redacted = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[CARD_NUMBER]", redacted)
        redacted = re.sub(r"\b\d{6}\b", "[PINCODE]", redacted)
        redacted = re.sub(r"\b\d{4}\b", "[FOUR_DIGITS]", redacted)
        redacted = re.sub(r"\b\d{3}\b", "[THREE_DIGITS]", redacted)
        redacted = re.sub(r"\b₹?\d+(?:\.\d{1,3})?\b", "[AMOUNT_OR_NUMBER]", redacted)
        return redacted.strip()
