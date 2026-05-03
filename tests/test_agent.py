from __future__ import annotations

from decimal import Decimal

from agent import Agent
from llm_assistant import MultiAgentTurnAnalysis, TurnGuidanceHint, TurnIntentHint
from models import AccountSnapshot, PaymentResult
from payment_api import AccountNotFoundError, ApiClientError


class FakeApiClient:
    def __init__(self) -> None:
        self.accounts = {
            "ACC1001": AccountSnapshot(
                account_id="ACC1001",
                full_name="Nithin Jain",
                dob="1990-05-14",
                aadhaar_last4="4321",
                pincode="400001",
                balance=Decimal("1250.75"),
            ),
            "ACC1003": AccountSnapshot(
                account_id="ACC1003",
                full_name="Priya Agarwal",
                dob="1992-08-10",
                aadhaar_last4="2468",
                pincode="400003",
                balance=Decimal("0.00"),
            ),
            "ACC1004": AccountSnapshot(
                account_id="ACC1004",
                full_name="Rahul Mehta",
                dob="1988-02-29",
                aadhaar_last4="1357",
                pincode="400004",
                balance=Decimal("3200.50"),
            ),
        }
        self.lookup_calls = []
        self.payment_calls = []

    def lookup_account(self, account_id: str) -> AccountSnapshot:
        self.lookup_calls.append(account_id)
        if account_id not in self.accounts:
            raise AccountNotFoundError("No account found.")
        return self.accounts[account_id]

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
        if card_number == "4000000000000002":
            return PaymentResult(success=False, error_code="invalid_card")
        if expiry_year < 2027:
            return PaymentResult(success=False, error_code="invalid_expiry")
        return PaymentResult(success=True, transaction_id="txn_test_123")


class FakeLlmAssistant:
    def __init__(
        self,
        intent: str,
        confidence: str = "high",
        guidance_agent: str | None = None,
        guidance_message: str | None = None,
    ) -> None:
        self.intent = intent
        self.confidence = confidence
        self.guidance_agent = guidance_agent
        self.guidance_message = guidance_message

    def analyze_turn(
        self, *, text: str, stage: str, account_loaded: bool, verified: bool
    ) -> MultiAgentTurnAnalysis:
        guidance_hint = None
        if self.guidance_agent:
            guidance_hint = TurnGuidanceHint(
                active_agent=self.guidance_agent,
                message=self.guidance_message,
                confidence=self.confidence,
            )
        return MultiAgentTurnAnalysis(
            intent_hint=TurnIntentHint(
                intent=self.intent,
                confidence=self.confidence,
                suggested_prompt=None,
            ),
            guidance_hint=guidance_hint,
        )


class LookupUnavailableApiClient(FakeApiClient):
    def lookup_account(self, account_id: str) -> AccountSnapshot:
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
    ) -> PaymentResult:
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


def test_happy_path_payment_success() -> None:
    agent = Agent(api_client=FakeApiClient())

    assert "account ID" in agent.next("Hi")["message"]
    assert "full name" in agent.next("My account ID is ACC1001")["message"]
    assert "one of these" in agent.next("Nithin Jain")["message"]
    assert "outstanding balance" in agent.next("DOB is 1990-05-14")["message"]
    assert "process ₹500.00" in agent.next("500")["message"]
    assert "card number" in agent.next("cardholder name is Nithin Jain")["message"]
    assert "CVV" in agent.next("4532015112830366")["message"]
    assert "expiry" in agent.next("123")["message"]
    success = agent.next("12/2027")["message"]
    assert "Payment successful" in success
    assert "txn_test_123" in success


def test_same_turn_lookup_and_verification_are_supported() -> None:
    agent = Agent(api_client=FakeApiClient())

    message = agent.next("ACC1001 Nithin Jain 1990-05-14")["message"]
    assert "outstanding balance" in message


def test_same_turn_lookup_and_bare_pincode_verifies() -> None:
    agent = Agent(api_client=FakeApiClient())

    message = agent.next("ACC1001 Nithin Jain 400001")["message"]

    assert "outstanding balance" in message


def test_verification_failure_locks_session_after_three_attempts() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1001")
    message = agent.next("Wrong Name, aadhaar 1234")["message"]
    assert "2 verification attempt" in message
    message = agent.next("Wrong Name, aadhaar 1234")["message"]
    assert "1 verification attempt" in message
    message = agent.next("Wrong Name, aadhaar 1234")["message"]
    assert "session is now closed" in message
    follow_up = agent.next("Nithin Jain 1990-05-14")["message"]
    assert "session is closed" in follow_up


def test_partial_verification_input_does_not_count_as_failure() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1001")
    message = agent.next("Nithin Jain")["message"]

    assert "verify your identity" in message
    assert agent.state.verification_failures == 0


def test_payment_failure_prompts_retry_for_card() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("400001")
    agent.next("500")
    agent.next("cardholder name is Nithin Jain")
    agent.next("4000000000000002")
    agent.next("123")
    message = agent.next("12/2027")["message"]
    assert "different valid card number" in message


def test_payment_retry_limit_closes_after_three_failures() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("400001")
    agent.next("500")
    agent.next("cardholder name is Nithin Jain")
    agent.next("4000000000000002")
    agent.next("123")
    agent.next("12/2027")
    agent.next("4000000000000002")
    agent.next("123")
    agent.next("12/2027")
    agent.next("4000000000000002")
    message = agent.next("123")["message"]

    assert "couldn't complete the payment after multiple attempts" in message
    assert agent.state.closed is True


def test_zero_balance_closes_without_payment_collection() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1003")
    agent.next("Priya Agarwal")
    message = agent.next("2468")["message"]
    assert "₹0.00" in message
    assert "no payment due" in message


def test_account_not_found_is_handled_cleanly() -> None:
    agent = Agent(api_client=FakeApiClient())

    message = agent.next("ACC9999")["message"]

    assert "couldn't find that account ID" in message
    assert agent.state.closed is False


def test_lookup_service_failure_closes_session() -> None:
    agent = Agent(api_client=LookupUnavailableApiClient())

    message = agent.next("ACC1001")["message"]

    assert "couldn't reach the account lookup service" in message
    assert agent.state.closed is True


def test_leap_year_dob_is_accepted_exactly() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1004")
    agent.next("Rahul Mehta")
    message = agent.next("1988-02-29")["message"]
    assert "outstanding balance" in message


def test_invalid_non_leap_dob_fails_verification() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1004")
    agent.next("Rahul Mehta")
    message = agent.next("1988-02-28")["message"]
    assert "did not match our records" in message


def test_amount_with_more_than_two_decimals_is_rejected_before_payment() -> None:
    api_client = FakeApiClient()
    agent = Agent(api_client=api_client)

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("400001")
    message = agent.next("500.999")["message"]

    assert "up to 2 decimal places" in message
    assert api_client.payment_calls == []


def test_card_details_are_not_retained_before_amount_is_set() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("400001")
    message = agent.next(
        "cardholder name is Nithin Jain, 4532015112830366, cvv 123, expiry 12/2027"
    )["message"]

    assert "amount you would like to pay" in message
    assert agent.state.card.cardholder_name is None
    assert agent.state.card.card_number is None
    assert agent.state.card.cvv is None
    assert agent.state.card.expiry_month is None
    assert agent.state.card.expiry_year is None


def test_agent_returns_only_message_field() -> None:
    agent = Agent(api_client=FakeApiClient())

    response = agent.next("ACC1001")

    assert set(response.keys()) == {"message"}
    assert "full name" in response["message"]


def test_post_verification_same_turn_payment_inputs_can_complete_payment() -> None:
    api_client = FakeApiClient()
    agent = Agent(api_client=api_client)

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("400001")
    response = agent.next(
        "Pay 500, cardholder name is Nithin Jain, 4532015112830366, cvv 123, expiry 12/2027"
    )

    assert "Payment successful" in response["message"]
    assert len(api_client.payment_calls) == 1


def test_payment_details_before_verification_do_not_trigger_payment_call() -> None:
    api_client = FakeApiClient()
    agent = Agent(api_client=api_client)

    message = agent.next(
        "ACC1001 pay 500 cardholder name is Nithin Jain 4532015112830366 cvv 123 expiry 12/2027"
    )["message"]

    assert "full name" in message
    assert api_client.payment_calls == []


def test_same_turn_after_verification_can_use_amount_and_card_inputs() -> None:
    api_client = FakeApiClient()
    agent = Agent(api_client=api_client)

    agent.next("ACC1001")
    message = agent.next(
        "Nithin Jain 1990-05-14 pay 250 cardholder name is Nithin Jain 4532015112830366 cvv 123 expiry 12/2027"
    )["message"]

    assert "Payment successful" in message
    assert len(api_client.payment_calls) == 1


def test_cancel_closes_session() -> None:
    agent = Agent(api_client=FakeApiClient())

    response = agent.next("cancel")

    assert "cancelled" in response["message"]
    assert agent.state.closed is True
    assert agent.state.stage == "cancelled"


def test_invalid_expiry_from_api_retries_from_expiry_without_losing_card_number() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("400001")
    agent.next("500")
    agent.next("cardholder name is Nithin Jain")
    agent.next("4532015112830366")
    agent.next("123")
    response = agent.next("12/2026")

    assert "expiry date was rejected" in response["message"]
    assert agent.state.stage == "awaiting_expiry"
    assert agent.state.card.card_number == "4532015112830366"


def test_payment_service_failure_closes_session() -> None:
    agent = Agent(api_client=PaymentUnavailableApiClient())

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("400001")
    agent.next("500")
    agent.next("cardholder name is Nithin Jain")
    agent.next("4532015112830366")
    agent.next("123")
    message = agent.next("12/2027")["message"]

    assert "couldn't reach the payment service" in message
    assert agent.state.closed is True


def test_leap_year_dob_is_valid_but_nearby_date_fails() -> None:
    agent = Agent(api_client=FakeApiClient())

    agent.next("ACC1004")
    agent.next("Rahul Mehta")
    valid = agent.next("1988-02-29")["message"]
    assert "outstanding balance" in valid

    agent = Agent(api_client=FakeApiClient())
    agent.next("ACC1004")
    agent.next("Rahul Mehta")
    invalid = agent.next("1988-02-28")["message"]
    assert "did not match our records" in invalid


def test_llm_hint_can_improve_missing_account_id_prompt() -> None:
    agent = Agent(
        api_client=FakeApiClient(),
        llm_assistant=FakeLlmAssistant(
            intent="account_lookup_attempt",
            guidance_agent="account_support",
            guidance_message="Please share your account ID in the format `ACC1001`.",
        ),
    )

    message = agent.next("my billing account is the same one as before")["message"]

    assert "account ID" in message


def test_llm_hint_can_improve_missing_verification_detail_prompt() -> None:
    agent = Agent(
        api_client=FakeApiClient(),
        llm_assistant=FakeLlmAssistant(
            intent="verification_attempt",
            guidance_agent="verification_support",
            guidance_message="Please verify with your date of birth (`YYYY-MM-DD`), Aadhaar last 4, or pincode.",
        ),
    )

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    message = agent.next("my details should already match")["message"]

    assert "Please verify with your date of birth" in message


def test_multi_agent_payment_support_hint_can_shape_amount_prompt() -> None:
    agent = Agent(
        api_client=FakeApiClient(),
        llm_assistant=FakeLlmAssistant(
            intent="payment_attempt",
            guidance_agent="payment_support",
            guidance_message="Please enter the payment amount as a number, for example `500`.",
        ),
    )

    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("400001")
    message = agent.next("I want to pay now")["message"]

    assert "payment amount as a number" in message


def test_client_reads_env_with_whitespace(monkeypatch) -> None:
    from payment_api import PaymentApiClient

    monkeypatch.setenv(
        "PAYMENT_API_BASE_URL",
        "\nhttps://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi\n",
    )
    client = PaymentApiClient(session=object())
    assert client.base_url == "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi"


def test_client_falls_back_when_openapi_path_returns_html_404() -> None:
    from payment_api import PaymentApiClient

    class FakeResponse:
        def __init__(self, status_code: int, body=None, json_error: bool = False) -> None:
            self.status_code = status_code
            self._body = body
            self._json_error = json_error

        def json(self):
            if self._json_error:
                raise ValueError("not json")
            return self._body

    class FakeSession:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url, json, timeout):
            self.calls.append(url)
            if url.endswith("/openapi/api/lookup-account"):
                return FakeResponse(404, json_error=True)
            return FakeResponse(
                200,
                {
                    "account_id": "ACC1001",
                    "full_name": "Nithin Jain",
                    "dob": "1990-05-14",
                    "aadhaar_last4": "4321",
                    "pincode": "400001",
                    "balance": 1250.75,
                },
            )

    session = FakeSession()
    client = PaymentApiClient(
        base_url="https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi",
        session=session,
    )
    account = client.lookup_account("ACC1001")
    assert account.account_id == "ACC1001"
    assert session.calls == [
        "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi/api/lookup-account",
        "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/api/lookup-account",
    ]


def test_client_raises_clean_error_for_malformed_lookup_payload() -> None:
    from payment_api import ApiClientError, PaymentApiClient

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"account_id": "ACC1001"}

    class FakeSession:
        def post(self, url, json, timeout):
            return FakeResponse()

    client = PaymentApiClient(session=FakeSession())
    try:
        client.lookup_account("ACC1001")
    except ApiClientError as exc:
        assert "unexpected response payload" in str(exc)
    else:
        raise AssertionError("Expected ApiClientError for malformed payload")
