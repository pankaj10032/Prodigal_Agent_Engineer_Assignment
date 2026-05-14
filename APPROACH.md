# Design Approach: Prodigal Payment Agent

## 1. Architecture Overview
The system follows a **Hybrid Agentic Architecture**. It separates the "Understanding" of natural language from the "Execution" of business rules.

### Components:
- **LLM Extraction Layer (`llm_assistant.py`)**: Uses OpenAI's Chat Completions to parse messy, conversational user input into a structured **Memory Dictionary**. This layer handles the ambiguity of human speech (e.g., date formats, currency words).
- **Deterministic Policy Engine (`agent.py`)**: A state-machine-like controller that enforces strict business rules. It decides when to call APIs, performs exact identity matching, and manages the retry counters.
- **State & Memory Management (`models.py`)**: Maintains the conversation history and a persistent memory of extracted entities. This allows the user to correct or delete information seamlessly.
- **External API Client (`payment_api.py`)**: Handles communication with the remote Lookup and Payment services, including error translation and route normalization.

---

## 2. Key Decisions & Rationale

### Decision: LLM for Extraction, Python for Verification
- **Why?** We decided NOT to let the LLM decide if a user is verified. LLMs are probabilistic; identity verification must be 100% deterministic. By extracting data first and then comparing it in Python, we ensure that "Nithin Jain" never matches "nithin jain" if the requirement is strict exact matching.
- **Benefit**: This architecture provides the flexibility of a chatbot with the safety of a bank-grade transaction system.

### Decision: Persistent Memory Dictionary
- **Why?** Real users change their minds (e.g., *"Wait, use my other card"*). By maintaining a `memory` dict that the LLM can update or clear, we handle corrections without the user having to restart the entire flow.

### Decision: Multi-Turn Context Injection
- **Why?** Instead of just passing the last message, we pass the recent history and the *current internal state* to the LLM. This prevents the agent from re-asking questions it already has the answers to.

---

## 3. Tradeoffs

- **Latency vs. Accuracy**: We perform two LLM calls per turn (Extraction then Response). While this adds ~2 seconds of latency, it significantly improves the accuracy of data capture compared to a single "do-it-all" prompt.
- **Strictness vs. Empathy**: We prioritize strictness for verification (Exact Match). While this might frustrate a user with a typo, it is a necessary tradeoff for the security requirements of a payment system.

---

## 4. Future Improvements

- **Local LLM Support**: Moving the extraction layer to a local model (like Llama 3) would reduce API costs and improve data privacy.
- **PII Redaction Layer**: Implementing an automated redaction layer that masks card numbers and Aadhaar details *before* they are logged or sent to the LLM for response generation.
- **Voice Integration**: The modular extraction layer is already well-suited to handle STT (Speech-to-Text) inputs for phone-based debt collection.
