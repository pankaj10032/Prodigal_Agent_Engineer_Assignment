# Approach

## Goal

Build a production-ready payment collection agent that is deterministic, safe around sensitive data, and compatible with an automated evaluator that repeatedly calls:

```python
Agent().next(user_input)
```

## Chosen Design

I intentionally used a deterministic controller instead of an LLM-orchestrated workflow.

### Why

- The task has a fixed sequence.
- Verification rules are strict and exact.
- The evaluator expects repeatable behavior.
- Payment and verification failures need explicit handling.
- Sensitive data should be handled with tight control.

This still uses a modern agent pattern, just without probabilistic planning: each turn is parsed into structured state and routed through an explicit policy. That gives the benefits of recent agent design work such as explicit belief state and traceable transitions, while keeping the runtime safe and predictable.

The evaluator for this assignment may be LLM-based, but the submitted agent does not need to call an LLM. I deliberately kept the core runtime independent from any LLM because a strict payment workflow should remain deterministic and easy to evaluate reliably.

To support a hybrid design, I added an optional multi-agent LLM layer that can be enabled explicitly with environment variables. It uses an OpenAI model only on redacted text and never as the source of truth for verification, validation, or tool execution.

## Architecture

The implementation is split into four layers:

### 1. `agent.py`

Owns the workflow, state transitions, retry limits, and user-facing responses.

### 2. `parsers.py`

Extracts structured fields from free-text turns, such as:

- account ID
- name
- DOB
- Aadhaar last 4
- pincode
- payment amount
- card details

This allows the agent to handle out-of-order inputs without turning the system into an unconstrained chatbot.
The policy layer still enforces the required order of operations, but it can consume multiple valid fields from a single user turn when doing so is safe.

### 3. `validators.py`

Runs local validation before any API call:

- account ID format
- date validity
- amount constraints
- card number length and Luhn check
- CVV rules
- expiry validity

### 4. `payment_api.py`

A thin HTTP client for:

- `lookup_account`
- `process_payment`

This keeps the agent logic separate from transport concerns and makes mocking easy in tests.

An additional optional module, `llm_assistant.py`, runs a small internal multi-agent system over the OpenAI Responses API. It is not required for the core flow.

## Conversation Model

The agent tracks conversation state in memory inside the `Agent` instance.

Key fields include:

- current stage
- looked-up account snapshot
- collected verification inputs
- verification retry count
- verified flag
- selected payment amount
- card data for the active payment attempt
- payment retry count
- closed flag
- recent tool traces
- bounded audit history

This satisfies the assignment requirement that state must be maintained internally between `next()` calls.

Because the required public interface must return exactly `{"message": str}`, structured outputs are kept internal to the system rather than exposed directly on `next()`. In practice that structure lives in the parser output, conversation state, API payload models, evaluation metrics, and the optional LLM multi-agent JSON-schema responses.

## Optional Multi-Agent LLM Layer

When enabled, the LLM layer uses several specialist roles:

- `triage agent`: classifies the redacted user turn and chooses the right specialist route
- `account support agent`: clarifies account lookup intent
- `verification coach agent`: clarifies missing verification information without revealing stored data
- `payment support agent`: clarifies the next payment detail
- `recovery agent`: generates safer fallback prompts for unclear turns

Each specialist returns a constrained structured output. The deterministic engine may use that hint to improve wording, but it does not delegate business logic or tool execution to the LLM.

## Verification Logic

Verification succeeds only when:

- `provided_full_name == account.full_name`
- and at least one of:
  - `provided_dob == account.dob`
  - `provided_aadhaar_last4 == account.aadhaar_last4`
  - `provided_pincode == account.pincode`

Important properties of the implementation:

- exact matching only for names
- no case folding for names
- no fuzzy matching
- partial inputs do not count as failures
- full failed verification attempts do count
- session locks after 3 failed attempts

The agent never reveals stored DOB, Aadhaar last 4, or pincode back to the user.

Interpretation note:

- The assignment asks both to avoid skipping steps and to handle out-of-order information without re-asking. I resolved that by preserving the business-process order internally while still consuming early user-provided fields when they can be validated and applied safely.

## Payment Logic

After verification:

1. Share outstanding balance.
2. Collect a payment amount less than or equal to the balance.
3. Collect cardholder name, card number, CVV, and expiry.
4. Validate locally before calling the payment API.
5. Process the payment.
6. Report success or a clear retry message.

Partial payments are supported.

## Security / Data Handling Choices

The assignment explicitly warns against unnecessary exposure or retention of sensitive data.

My choices:

- Do not echo stored verification attributes back to the user.
- Do not proceed to payment before verification.
- Do not skip verification or payment validation even when the user provides future-step fields early.
- Do not persist state outside the process.
- Do not retain raw PAN or CVV until a valid payment amount exists.
- Clear payment fields from memory when a payment attempt completes or the session closes.
- Keep behavior deterministic so failures are explainable and testable.
- If the optional LLM assist layer is enabled, redact sensitive values before sending user text to the model.

## Failure Handling

### Account lookup

- Invalid format: ask for a valid `ACC...` ID.
- Unknown account: ask for a different account ID.
- Service failure: close cleanly and ask the user to start a new session later.

### Verification

- Partial data: guide the user toward what is still needed.
- Failed match: increment retries and ask for a fresh attempt.
- Retry limit reached: close the session.

### Payment

- Invalid local input: do not call the API.
- API validation failure: explain the issue and ask for the relevant field again.
- Repeated payment failures: close after 3 failed API attempts.
- Service outage: close cleanly.

## Tradeoffs

### Accepted tradeoffs

- Parsing is intentionally conservative and regex-based.
- The conversation is structured rather than highly naturalistic.
- Card retries are safe but somewhat strict because sensitive fields are cleared aggressively.
- The public interface is intentionally minimal because the evaluator requires exactly `{"message": str}`.
- The optional multi-agent LLM layer is advisory only, so it improves flexibility without weakening deterministic guarantees.

### Why those tradeoffs are acceptable

- Determinism matters more than stylistic flexibility for this assignment.
- Explicit flow control reduces evaluator flakiness.
- Security and correctness were prioritized over conversational polish.

## Evaluation Strategy

I included:

- pytest-based automated tests
- a small scenario runner in `evaluate.py`

Covered scenarios:

- successful end-to-end payment
- verification lockout
- payment failure
- account-not-found recovery
- lookup-service failure
- payment-service failure
- zero-balance closure
- leap-year DOB edge case
- out-of-order same-turn input handling
- early card-data submission before amount selection
- partial verification input without counting a failed attempt
- payment retry-limit closure
- mocked optional multi-agent LLM behavior

The standalone evaluation script also checks more than final-message matching. It scores:

- response-content correctness
- lookup call-count correctness
- payment call-count correctness
- lookup payload correctness
- payment payload correctness
- terminal-state correctness

## With More Time

- Add a richer parser for multi-field free-text turns.
- Add structured logging with sensitive-field redaction.
- Add more exhaustive negative tests around parsing ambiguity.
- Add a formal state-transition table and richer evaluation reporting beyond the current scenario and tool-call metrics.
