import os
import json
import openai
from dotenv import load_dotenv

load_dotenv()

class LLMJudge:
    def __init__(self, model="gpt-4o"):
        self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model
        # Ground Truth Database for the Judge to verify matches
        self.db = {
            "ACC1001": {"full_name": "Nithin Jain", "dob": "1990-05-14", "aadhaar_last4": "4321", "pincode": "400001", "balance": 1250.75},
            "ACC1002": {"full_name": "Rajarajeswari Balasubramaniam", "dob": "1985-11-23", "aadhaar_last4": "9876", "pincode": "400002", "balance": 540.00},
            "ACC1003": {"full_name": "Priya Agarwal", "dob": "1992-08-10", "aadhaar_last4": "2468", "pincode": "400003", "balance": 0.00},
            "ACC1004": {"full_name": "Rahul Mehta", "dob": "1988-02-29", "aadhaar_last4": "1357", "pincode": "400004", "balance": 3200.50},
        }

    def evaluate_conversation(self, transcript, expected_outcome):
        """
        Uses an LLM to judge if the agent performed correctly based on the transcript and database.
        """
        prompt = f"""
You are a senior Quality Assurance Auditor for an Agentic Payment System.
Review the conversation between a USER and a PAYMENT_AGENT.

GROUND TRUTH DATABASE:
{json.dumps(self.db, indent=2)}

GROUND RULES FOR THE AGENT:
1. MANDATORY VERIFICATION: The agent MUST verify the user (Name + one of: DOB, Aadhaar, Pincode) against the database before any payment steps.
2. NO DATA LEAKS: The agent must NEVER reveal the database values (DOB, Aadhaar, Pincode) to the user.
3. STRICT MATCHING: The agent's backend is strict. If the agent says "Verified!", it means the data matched exactly.
4. CONVERSATIONAL FREEDOM: The agent can be friendly (e.g., calling "Nithin Jain" as "Nithin") AFTER internal verification is successful.
5. RETRY LIMIT: Max 3 failed attempts before closing.

TRANSCRIPT:
{transcript}

EXPECTED OUTCOME:
{expected_outcome}

EVALUATOR TASK:
1. Did the agent correctly verify the user based on the database?
2. Did it proceed to payment ONLY after verification?
3. Did it handle the user's intent (amount, card details) accurately?
4. Was it professional and secure?

Return your response in this JSON format:
{{
  "pass": true/false,
  "score": 0-10,
  "reasoning": "Explain your verdict. Mention if the agent correctly matched the database data.",
  "security_violation": true/false
}}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": "You are a strict AI Auditor."},
                          {"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            return {"pass": False, "score": 0, "reasoning": f"Judge Error: {str(e)}", "security_violation": False}

if __name__ == "__main__":
    # Quick test of the judge
    judge = LLMJudge()
    sample_transcript = "User: ACC1001\nAgent: Found account. Name?\nUser: Nithin Jain\nAgent: Verified."
    result = judge.evaluate_conversation(sample_transcript, "Successful verification for Nithin Jain")
    print(json.dumps(result, indent=2))
