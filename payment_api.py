from __future__ import annotations

from decimal import Decimal
import os
from typing import Optional

import requests

from models import AccountSnapshot, PaymentResult


DEFAULT_BASE_URL = "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi"


class ApiClientError(Exception):
    pass


class AccountNotFoundError(ApiClientError):
    pass


class PaymentApiClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: int = 15,
        session: Optional[requests.Session] = None,
    ) -> None:
        configured_base_url = base_url or os.getenv("PAYMENT_API_BASE_URL") or DEFAULT_BASE_URL
        self.base_url = self._normalize_base_url(configured_base_url)
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def lookup_account(self, account_id: str) -> AccountSnapshot:
        response = self._post("/api/lookup-account", {"account_id": account_id})
        payload = self._json_body(response)
        if response.status_code == 404:
            if payload:
                raise AccountNotFoundError(payload.get("message", "Account not found."))
            raise ApiClientError(
                f"Lookup endpoint returned {response.status_code} with a non-JSON response. "
                f"Please verify PAYMENT_API_BASE_URL. Current base URL: {self.base_url}"
            )
        if response.status_code != 200:
            raise ApiClientError(f"Lookup failed with status {response.status_code}.")
        if not payload:
            raise ApiClientError(
                f"Lookup endpoint returned {response.status_code} with a non-JSON response. "
                f"Please verify PAYMENT_API_BASE_URL. Current base URL: {self.base_url}"
            )

        try:
            return AccountSnapshot(
                account_id=payload["account_id"],
                full_name=payload["full_name"],
                dob=payload["dob"],
                aadhaar_last4=payload["aadhaar_last4"],
                pincode=payload["pincode"],
                balance=Decimal(str(payload["balance"])),
            )
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise ApiClientError("Lookup service returned an unexpected response payload.") from exc

    def process_payment(
        self,
        *,
        account_id: str,
        amount: Decimal,
        cardholder_name: str,
        card_number: str,
        cvv: str,
        expiry_month: int,
        expiry_year: int,
    ) -> PaymentResult:
        payload = {
            "account_id": account_id,
            "amount": float(amount),
            "payment_method": {
                "type": "card",
                "card": {
                    "cardholder_name": cardholder_name,
                    "card_number": card_number,
                    "cvv": cvv,
                    "expiry_month": expiry_month,
                    "expiry_year": expiry_year,
                },
            },
        }
        response = self._post("/api/process-payment", payload)
        body = self._json_body(response)
        if response.status_code == 200:
            if not body:
                raise ApiClientError(
                    f"Payment endpoint returned {response.status_code} with a non-JSON response. "
                    f"Please verify PAYMENT_API_BASE_URL. Current base URL: {self.base_url}"
                )
            try:
                return PaymentResult(
                    success=bool(body.get("success")),
                    transaction_id=body.get("transaction_id"),
                )
            except AttributeError as exc:
                raise ApiClientError("Payment service returned an unexpected response payload.") from exc
        if response.status_code == 422:
            if not body:
                raise ApiClientError(
                    f"Payment endpoint returned {response.status_code} with a non-JSON response. "
                    f"Please verify PAYMENT_API_BASE_URL. Current base URL: {self.base_url}"
                )
            try:
                return PaymentResult(
                    success=False,
                    error_code=body.get("error_code"),
                    message=body.get("message"),
                )
            except AttributeError as exc:
                raise ApiClientError("Payment service returned an unexpected response payload.") from exc
        raise ApiClientError(f"Payment failed with status {response.status_code}.")

    def _post(self, path: str, payload: dict) -> requests.Response:
        last_response: Optional[requests.Response] = None
        for base_url in self._candidate_base_urls():
            url = f"{base_url}{path}"
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                raise ApiClientError("Payment service is unavailable.") from exc
            last_response = response
            if self._looks_like_route_mismatch(response):
                continue
            return response

        assert last_response is not None
        return last_response

    def _candidate_base_urls(self) -> list[str]:
        candidates = [self.base_url]
        if self.base_url.endswith("/openapi"):
            candidates.append(self.base_url[: -len("/openapi")])
        return candidates

    def _normalize_base_url(self, base_url: str) -> str:
        return base_url.strip().rstrip("/")

    def _json_body(self, response: requests.Response) -> Optional[dict]:
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def _looks_like_route_mismatch(self, response: requests.Response) -> bool:
        if response.status_code not in {404, 405}:
            return False
        return self._json_body(response) is None
