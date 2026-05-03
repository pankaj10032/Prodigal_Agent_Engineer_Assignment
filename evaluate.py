from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional

from agent import Agent
from payment_api import ApiClientError
from tests.test_agent import FakeApiClient


class LookupUnavailableApiClient(FakeApiClient):
    def lookup_account(self, account_id: str):
        self.lookup_calls.append(account_id)
        raise ApiClientError("lookup unavailable")


class PaymentUnavailableApiClient(FakeApiClient):
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
    ):
        self.payment_calls.append(
            {
                "account_id": account_id,
                "amount": amount,
                "cardholder_name": cardholder_name,
                "card_number": card_number,
                "cvv": cvv,
                "expiry_month": expiry_month,
                "expiry_year": expiry_year,
            }
        )
        raise ApiClientError("payment unavailable")


@dataclass
class Scenario:
    name: str
    turns: list[str]
    expected_substrings: list[str]
    client_factory: Callable[[], FakeApiClient] = FakeApiClient
    expected_lookup_calls: Optional[int] = None
    expected_payment_calls: Optional[int] = None
    expected_lookup_sequence: Optional[list[str]] = None
    expected_payment_amount: Optional[Decimal] = None
    expected_payment_account_id: Optional[str] = None
    expect_closed: Optional[bool] = None


def run_scenario(scenario: Scenario) -> dict:
    api_client = scenario.client_factory()
    agent = Agent(api_client=api_client)
    last_message = ""
    for turn in scenario.turns:
        last_message = agent.next(turn)["message"]

    lookup_calls = len(api_client.lookup_calls)
    payment_calls = len(api_client.payment_calls)
    last_payment_call = api_client.payment_calls[-1] if api_client.payment_calls else None

    content_ok = all(substring in last_message for substring in scenario.expected_substrings)
    lookup_count_ok = (
        scenario.expected_lookup_calls is None or lookup_calls == scenario.expected_lookup_calls
    )
    payment_count_ok = (
        scenario.expected_payment_calls is None or payment_calls == scenario.expected_payment_calls
    )
    lookup_sequence_ok = (
        scenario.expected_lookup_sequence is None or api_client.lookup_calls == scenario.expected_lookup_sequence
    )
    payment_amount_ok = (
        scenario.expected_payment_amount is None
        or (last_payment_call is not None and last_payment_call["amount"] == scenario.expected_payment_amount)
    )
    payment_account_ok = (
        scenario.expected_payment_account_id is None
        or (
            last_payment_call is not None
            and last_payment_call["account_id"] == scenario.expected_payment_account_id
        )
    )
    closed_ok = scenario.expect_closed is None or agent.state.closed is scenario.expect_closed

    return {
        "content_ok": content_ok,
        "lookup_count_ok": lookup_count_ok,
        "payment_count_ok": payment_count_ok,
        "lookup_sequence_ok": lookup_sequence_ok,
        "payment_amount_ok": payment_amount_ok,
        "payment_account_ok": payment_account_ok,
        "closed_ok": closed_ok,
        "lookup_calls": lookup_calls,
        "payment_calls": payment_calls,
        "final_message": last_message,
    }


def main() -> None:
    scenarios = [
        Scenario(
            name="happy_path",
            turns=[
                "ACC1001",
                "Nithin Jain",
                "1990-05-14",
                "500",
                "cardholder name is Nithin Jain",
                "4532015112830366",
                "123",
                "12/2027",
            ],
            expected_substrings=["Payment successful", "txn_test_123"],
            expected_lookup_calls=1,
            expected_payment_calls=1,
            expected_lookup_sequence=["ACC1001"],
            expected_payment_amount=Decimal("500"),
            expected_payment_account_id="ACC1001",
            expect_closed=True,
        ),
        Scenario(
            name="verification_lockout",
            turns=["ACC1001", "Wrong Name 1234", "Wrong Name 1234", "Wrong Name 1234"],
            expected_substrings=["session is now closed"],
            expected_lookup_calls=1,
            expected_payment_calls=0,
            expected_lookup_sequence=["ACC1001"],
            expect_closed=True,
        ),
        Scenario(
            name="payment_failure_invalid_card_retryable",
            turns=[
                "ACC1001",
                "Nithin Jain",
                "400001",
                "500",
                "cardholder name is Nithin Jain",
                "4000000000000002",
                "123",
                "12/2027",
            ],
            expected_substrings=["different valid card number"],
            expected_lookup_calls=1,
            expected_payment_calls=1,
            expected_lookup_sequence=["ACC1001"],
            expected_payment_amount=Decimal("500"),
            expected_payment_account_id="ACC1001",
            expect_closed=False,
        ),
        Scenario(
            name="zero_balance",
            turns=["ACC1003", "Priya Agarwal", "2468"],
            expected_substrings=["no payment due"],
            expected_lookup_calls=1,
            expected_payment_calls=0,
            expected_lookup_sequence=["ACC1003"],
            expect_closed=True,
        ),
        Scenario(
            name="account_not_found",
            turns=["ACC9999"],
            expected_substrings=["couldn't find that account ID"],
            expected_lookup_calls=1,
            expected_payment_calls=0,
            expected_lookup_sequence=["ACC9999"],
            expect_closed=False,
        ),
        Scenario(
            name="lookup_service_unavailable",
            turns=["ACC1001"],
            expected_substrings=["couldn't reach the account lookup service"],
            client_factory=LookupUnavailableApiClient,
            expected_lookup_calls=1,
            expected_payment_calls=0,
            expected_lookup_sequence=["ACC1001"],
            expect_closed=True,
        ),
        Scenario(
            name="payment_service_unavailable",
            turns=[
                "ACC1001",
                "Nithin Jain",
                "400001",
                "500",
                "cardholder name is Nithin Jain",
                "4532015112830366",
                "123",
                "12/2027",
            ],
            expected_substrings=["couldn't reach the payment service"],
            client_factory=PaymentUnavailableApiClient,
            expected_lookup_calls=1,
            expected_payment_calls=1,
            expected_lookup_sequence=["ACC1001"],
            expected_payment_amount=Decimal("500"),
            expected_payment_account_id="ACC1001",
            expect_closed=True,
        ),
        Scenario(
            name="out_of_order_same_turn_success",
            turns=[
                "ACC1001 Nithin Jain 1990-05-14 pay 250, cardholder name is Nithin Jain, 4532015112830366, cvv 123, expiry 12/2027"
            ],
            expected_substrings=["Payment successful", "₹250.00"],
            expected_lookup_calls=1,
            expected_payment_calls=1,
            expected_lookup_sequence=["ACC1001"],
            expected_payment_amount=Decimal("250"),
            expected_payment_account_id="ACC1001",
            expect_closed=True,
        ),
    ]

    passed = 0
    content_correct = 0
    lookup_count_correct = 0
    payment_count_correct = 0
    lookup_payload_correct = 0
    payment_payload_correct = 0
    closure_correct = 0
    total = len(scenarios)

    for scenario in scenarios:
        result = run_scenario(scenario)
        ok = all(
            result[key]
            for key in (
                "content_ok",
                "lookup_count_ok",
                "payment_count_ok",
                "lookup_sequence_ok",
                "payment_amount_ok",
                "payment_account_ok",
                "closed_ok",
            )
        )
        status = "PASS" if ok else "FAIL"
        print(
            f"{status}: {scenario.name} | "
            f"lookup_calls={result['lookup_calls']} payment_calls={result['payment_calls']}"
        )
        if result["content_ok"]:
            content_correct += 1
        if result["lookup_count_ok"]:
            lookup_count_correct += 1
        if result["payment_count_ok"]:
            payment_count_correct += 1
        if result["lookup_sequence_ok"]:
            lookup_payload_correct += 1
        if result["payment_amount_ok"] and result["payment_account_ok"]:
            payment_payload_correct += 1
        if result["closed_ok"]:
            closure_correct += 1
        if ok:
            passed += 1

    print(f"\nScenario success rate: {passed}/{total}")
    print(f"Response-content correctness: {content_correct}/{total}")
    print(f"Lookup call-count correctness: {lookup_count_correct}/{total}")
    print(f"Payment call-count correctness: {payment_count_correct}/{total}")
    print(f"Lookup payload correctness: {lookup_payload_correct}/{total}")
    print(f"Payment payload correctness: {payment_payload_correct}/{total}")
    print(f"Terminal-state correctness: {closure_correct}/{total}")


if __name__ == "__main__":
    main()
