## 1. Defining "Correctness" in Detail
Correctness is not just a boolean state; it is a behavioral contract across the multi-turn lifecycle:

- **Account Lookup Phase**:
  - *Correct*: Extracting `ACC1001` from *"Hey my id is acc 1001"*.
  - *Correct*: Handling 404 errors by asking the user to re-check their ID without crashing.
- **Verification Phase (The "Hard Gate")**:
  - *Correct*: Rejecting *"Nith Jain"* for *"Nithin Jain"* (Strict matching).
  - *Correct*: Accepting any **one** of the three secondary factors (DOB, Aadhaar, Pincode).
  - *Correct*: Resetting the verification state if the user changes their Account ID mid-flow.
- **Payment Collection Phase**:
  - *Correct*: Converting natural language amounts (*"pay five hundred"*) to `Decimal("500.00")`.
  - *Correct*: Clearing all sensitive card data from the state immediately after the API call (Success or Failure).
- **Global Error Handling**:
  - *Correct*: Identifying the difference between a user error (wrong CVV) and a system error (API down).
  - *Correct*: Enforcing a global 3-attempt cap per field to prevent brute-force or infinite loops.

---

## 2. Metrics for Success

| Metric | Target | Description |
| :--- | :--- | :--- |
| **Task Success Rate (TSR)** | 98% | % of sessions reaching the correct terminal state (Success or Lockout). |
| **Extraction Accuracy** | 95% | % of entities correctly parsed from unstructured, messy user input. |
| **Policy Adherence** | 100% | Zero tolerance for skipping verification or reveals of sensitive data. |
| **Recovery Rate** | 90% | Success in guiding users to fix invalid inputs (e.g., incorrect CVV length). |
| **Average Turn Count** | 4.2 | Efficiency of the path from greeting to transaction ID. |

---

## 3. Automated Evaluation Framework: AI-as-a-Judge

We use a sophisticated **LLM-as-a-Judge** framework (implemented in `tests/llm_judge.py`) to audit our agent. Unlike simple keyword matching, this approach uses a high-reasoning model (GPT-4o) to evaluate the conversation transcript.

### Why "Agent-as-Evaluator"?
Traditional testing struggles with the "variability" of LLM responses. A deterministic test might fail if the agent says *"I've processed that"* instead of *"Payment successful"*. Our AI Judge understands **intent** and **policy compliance**.

### Audit Checklist for the AI Judge:
1.  **Verification Before Payment**: Did the agent attempt a payment BEFORE the verification audit event? (Auto-fail if Yes).
2.  **PII Leakage**: Did the agent ever mention the user's DOB or Aadhaar digits back to them? (Auto-fail for Security).
3.  **Strictness**: Did the agent let a minor name typo slide? (Auto-fail for Policy).
4.  **Empathy & Clarity**: Did the agent explain API errors (like `insufficient_balance`) in human terms?

### Test Suite: `tests/test_agent_scenarios.py`
This consolidated suite runs 50+ real scenarios through the AI Judge. It covers:
- **Category 1-2**: Lookup & Verification (Messy IDs, Regional dates).
- **Category 3**: Security Enforcement (Lockouts).
- **Category 4**: Memory Resilience (Corrections & Change of mind).
- **Category 5-7**: Payment Logic (Words-to-Numbers, Card validation, API errors).
- **Category 8-9**: Conversational Robustness (Interruptions, Security questions).

---

## 4. Observations & Struggle Areas

### Performance Strengths:
- **Resilience to "Messy" Inputs**: The agent is exceptionally good at extracting account IDs and dates from natural sentences like *"my id is acc 1001 and i was born in 1990 may 14th"*.
- **Memory Consistency**: The system handles "corrections" flawlessly. If a user says *"Wait, my full name is actually..."*, the agent updates its memory and proceeds without losing the previously provided account ID.

### Identified Struggle Areas:
- **Ambiguous Dates**: If a user provides two dates (e.g., today's date and their DOB) in the same sentence, the extraction layer occasionally requires a second turn to disambiguate.
- **Extreme Slang**: While "₹" and "bucks" are handled, highly informal regional slang for currency might occasionally lead to an "invalid amount" prompt.
- **Latency**: Using a high-reasoning extraction layer adds ~1-2 seconds of latency per turn. This is a tradeoff we accepted to ensure 100% policy compliance.

---

## 5. Automated Evaluation Script
The primary entry point for evaluation is `tests/test_agent_scenarios.py`. It generates a comprehensive report for all 50 scenarios, providing a "Verdict" and "Reasoning" for every interaction.
