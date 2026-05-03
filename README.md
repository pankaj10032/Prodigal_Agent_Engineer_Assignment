# Payment Collection Agent

A deterministic, production-oriented payment collection agent for the Prodigal take-home assignment.

The agent exposes the required interface:

```python
class Agent:
    def next(self, user_input: str) -> dict:
        return {"message": "..."}
```

It handles:

- account lookup
- strict identity verification
- outstanding balance disclosure only after verification
- partial payment collection
- card payment processing
- clear failure handling and session closure
- optional OpenAI-powered multi-agent assistance with deterministic fallback
- minimal retention of sensitive card data during collection

See [APPROACH.md](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/APPROACH.md) for the design write-up and [ERD.md](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/ERD.md) for the data model sketch.

## Files

- [agent.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/agent.py): required `Agent` interface and workflow state machine
- [payment_api.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/payment_api.py): HTTP client for account lookup and payment processing
- [llm_assistant.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/llm_assistant.py): optional OpenAI Responses API multi-agent orchestration on redacted text
- [validators.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/validators.py): strict input validation helpers
- [parsers.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/parsers.py): deterministic field extraction from free-text turns
- [models.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/models.py): conversation and API data models
- [tests/test_agent.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/tests/test_agent.py): automated tests
- [evaluate.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/evaluate.py): simple scenario-based evaluation runner
- [cli.py](/Users/panka/Desktop/2026-PROJECT/Prodigal-Assignment/cli.py): optional interactive CLI

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The default API base URL is:

```text
https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi
```

The client automatically falls back to the non-`/openapi` path if the documented base URL returns a route-level 404/non-JSON response. A live smoke test against the provided service confirmed that this fallback matters in practice.

You usually do not need to override it. If you do, use a single-line export:

```bash
export PAYMENT_API_BASE_URL="https://..."
```

Do not include leading/trailing whitespace or a line break inside the quoted value.

## Usage

### Required interface

```python
from agent import Agent

agent = Agent()
response = agent.next("My account ID is ACC1001")
print(response["message"])
```

### Interactive CLI

```bash
python3 cli.py
```

### Optional Multi-Agent LLM Assist

The core agent does not require an LLM and works fully without OpenAI credentials. If you want to enable the optional multi-agent LLM layer, set:

```bash
export ENABLE_LLM_ASSIST=1
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-5.4-mini"
```

The multi-agent LLM path is intentionally non-authoritative:

- deterministic validation and state transitions remain the source of truth
- the model only sees a redacted version of the user message
- raw account IDs, DOBs, Aadhaar digits, pincodes, PAN, and CVV are not sent to the LLM
- if the LLM is unavailable, the agent falls back silently to the rule-based parser

The internal LLM roles are:

- `triage agent`: classifies the turn and routes it to the right specialist
- `account support agent`: helps clarify missing account-ID intent
- `verification coach agent`: helps clarify what verification detail is still needed
- `payment support agent`: helps clarify the next payment detail
- `recovery agent`: produces safer recovery prompts for unclear turns

### Run tests

```bash
pytest -q
```

### Run evaluation script

```bash
python3 evaluate.py
```

## Design Summary

This solution uses a rule-based state machine rather than an LLM-driven planner. That choice was intentional:

- verification is strict and deterministic
- payment flow ordering is fixed
- evaluator behavior should be stable across runs
- sensitive data handling is easier to control with explicit logic

The flow borrows a modern agent pattern without making the runtime probabilistic: each turn is reduced into a compact internal belief state and routed through explicit policy logic. In practice that is a better fit for payment collection than an unconstrained planner.

The public interface stays exactly as required: every `next()` call returns only `{"message": str}`. The implementation keeps richer state internally.

The evaluator for this assignment may be LLM-based, but the agent itself does not need to call an LLM. This implementation does not require any runtime LLM dependency.

I also added an optional OpenAI-backed multi-agent layer for users who want an LLM in the loop. It uses the Responses API with Structured Outputs and specialized internal roles on redacted text, but it never decides verification outcomes, payment validity, or tool payloads on its own.

The agent still supports out-of-order information. If a user provides account ID, verification data, amount, and even card details in the same turn, the agent processes them in the correct order internally. For security, card details are not retained before a valid payment amount exists.

## Assumptions

- Account IDs are accepted in the format `ACC` followed by digits.
- Name matching is exact and case-sensitive, as required.
- Verification failures are limited to 3 completed failed attempts.
- Partial verification input does not count as a failed attempt.
- Payment API failures are retried up to 3 times before the session is closed.
- Raw card number and CVV are only retained once a valid amount exists, and all payment fields are cleared when the session closes or a payment attempt completes.
- “Do not skip steps” is interpreted as “do not skip validation, verification, or required business rules.” The agent may still consume out-of-order user input in a single turn if it can apply those fields safely in the required order without re-asking.
- “Structured outputs” are implemented internally through typed state/models and, when enabled, JSON-schema-constrained LLM outputs. The public `next()` response remains exactly `{"message": str}` to match the required evaluator interface.

## Sample Conversations

### 1. Successful payment

```text
User: Hi
Agent: Hello! Please share your account ID to get started.

User: ACC1001
Agent: Got it. Please confirm your full name exactly as it appears on the account.

User: Nithin Jain
Agent: Thanks. Please verify your identity with one of these: date of birth (`YYYY-MM-DD`), Aadhaar last 4, or pincode.

User: 1990-05-14
Agent: Identity verified. Your outstanding balance is ₹1,250.75. How much would you like to pay today?

User: 500
Agent: Got it. I can process ₹500.00. Please share the cardholder name exactly as it appears on the card.

User: cardholder name is Nithin Jain
Agent: Please share your card number.

User: 4532015112830366
Agent: Please share the card CVV.

User: 123
Agent: Please share the card expiry in `MM/YYYY` format.

User: 12/2027
Agent: Payment successful. Transaction ID: `txn_test_123`. I processed ₹500.00 for account `ACC1001`. This session is now complete.
```

### 2. Verification failure

```text
User: ACC1001
Agent: Got it. Please confirm your full name exactly as it appears on the account.

User: Wrong Name, aadhaar 1234
Agent: That information did not match our records. You have 2 verification attempt(s) remaining. Please re-enter your full name and one of: date of birth, Aadhaar last 4, or pincode.

User: Wrong Name, aadhaar 1234
Agent: That information did not match our records. You have 1 verification attempt(s) remaining. Please re-enter your full name and one of: date of birth, Aadhaar last 4, or pincode.

User: Wrong Name, aadhaar 1234
Agent: I couldn't verify your identity after 3 attempts, so this session is now closed. Please start a new session if you still need help.
```

### 3. Payment failure

```text
User: ACC1001
User: Nithin Jain
User: 400001
User: 500
User: cardholder name is Nithin Jain
User: 4000000000000002
User: 123
User: 12/2027
Agent: The card was rejected by the payment service. Please enter a different valid card number.
```

Note: this scenario is exercised with the mocked API client in tests so the agent's retry path is covered deterministically even when live-service validation behavior differs.

### 4. Edge case: zero balance

```text
User: ACC1003
User: Priya Agarwal
User: 2468
Agent: Identity verified. Your outstanding balance is ₹0.00, so there is no payment due right now. This session is now closed.
```

## Evaluation Approach

Automated tests and the evaluation script cover:

- happy path success
- verification lockout
- payment API failure
- account-not-found recovery
- lookup-service terminal failure
- payment-service terminal failure
- zero-balance closure
- leap-year DOB handling
- out-of-order same-turn information handling
- early card-data submission without a valid amount
- partial verification input without counting a failed attempt
- payment retry-limit closure after repeated rejected cards
- optional multi-agent LLM hinting path via mocked specialist-agent tests

Correctness is measured by:

- stage order is enforced
- APIs are called only after local validation
- strict verification rules are respected
- sensitive account fields are not echoed back
- terminal states are stable across repeated calls
- lookup payloads use the correct account IDs
- payment payloads use the correct account IDs and amounts

The evaluation runner prints simple metrics:

- scenario success rate
- response-content correctness
- lookup call-count correctness
- payment call-count correctness
- lookup payload correctness
- payment payload correctness
- terminal-state correctness

## What I Would Improve With More Time

- stronger natural-language extraction without sacrificing determinism
- richer payment retry recovery that preserves only non-sensitive fields
- structured audit events for observability without storing raw PAN/CVV
- property-based tests for parser edge cases
