# Payment Collection AI Agent

An agentic, multi-turn AI system for handling debt collection and payment processing, built for the Prodigal Agent Engineer Assignment.

---

## 🛠️ Quick Start

1.  **Clone the repository** and navigate to the directory.
2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Configure environment variables**:
    Create a `.env` file (or use the provided one):
    ```bash
    OPENAI_API_KEY="your-api-key"
    OPENAI_MODEL="gpt-4o"
    PAYMENT_API_BASE_URL="https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi"
    ```
4.  **Run the CLI**:
    ```bash
    python cli.py
    ```

---

## 🏛️ Architecture & Design

For a detailed breakdown of the system architecture, key decisions, and tradeoffs, please see the **[APPROACH.md](APPROACH.md)** document.

---

## 🧪 Evaluation Strategy

### Automated Scenarios (50 Test Cases)
Run the comprehensive real-world test suite with:
```bash
python tests/test_agent_scenarios.py
```
**Categories covered**:
- **Identity**: Exact name matching, Leap year logic (`ACC1004`), and regional date formats.
- **Security**: Strict 3-fail lockout enforcement and data masking.
- **Edge Cases**: Change of mind (*"Wait, I mean ACC1001"*), one-shot verification, and partial payments.
- **API Errors**: Handling of `insufficient_balance`, `invalid_card`, and `invalid_cvv`.

### Success Metrics
- **Task Success Rate (TSR)**: % of sessions reaching a terminal state correctly.
- **Extraction Precision**: Accuracy of LLM-based data capture.
- **Policy Adherence**: 100% compliance with Hard Rules (No payment before verification).

---

## 📜 Sample Conversations

### 1. Successful Payment
**User**: *"it's ACC1001"*  
**Agent**: Found account ACC1001. Please provide your full name and Aadhaar last 4 for verification.  
**User**: *"My name is Nithin Jain and Aadhaar is 4321"*  
**Agent**: Verified! Your balance is ₹1,250.75. (Continues to payment...)

### 2. Verification Failure (Lockout)
**User**: *"ACC1001"*  
**User**: *"Pankaj Goyal, 9999"* (Incorrect)  
**Agent**: Details don't match. 2 attempts left.  
**User**: (Fails 2 more times)  
**Agent**: Verification failed 3 times. Session locked.

### 3. Edge Case: Correction
**User**: *"My name is Rahul"*  
**User**: *"Wait, use my full name Rahul Mehta instead"*  
**Agent**: (Updates memory) Got it, Rahul Mehta. (Proceeds with updated name).

---

## 📂 Project Structure
- `agent.py`: Core Agent class and interface.
- `llm_assistant.py`: OpenAI integration.
- `payment_api.py`: API Client for Lookup/Payment.
- `models.py`: Data structures and State.
- `validators.py`: Strict local validation logic.
- `cli.py`: Interactive CLI tool.
