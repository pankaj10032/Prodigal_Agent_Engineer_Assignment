import sys
import os
import json
import logging
import pytest
from decimal import Decimal
from pathlib import Path

# Add project root to sys.path to allow running from within tests/ folder
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))

from agent import Agent
from payment_api import PaymentApiClient
from llm_assistant import OpenAILLMMultiAgent
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger("ScenarioTest")

# Load env for OpenAI API Key
load_dotenv()

TEST_CASES = [
    # --- Category 1: Account Lookup (Messy IDs & Errors) ---
    {"name": "L1: Natural ID", "turns": ["my account is ACC1001"], "expect_stage": "awaiting_verification"},
    {"name": "L2: Spaced ID", "turns": ["A C C 1 0 0 1"], "expect_stage": "awaiting_verification"},
    {"name": "L3: Uppercase/Lowercase ID", "turns": ["acc1001"], "expect_stage": "awaiting_verification"},
    {"name": "L4: ID in middle of text", "turns": ["yeah i think it is ACC1001, let me check"], "expect_stage": "awaiting_verification"},
    {"name": "L5: Not Found ID", "turns": ["ACC9999"], "expect_stage": "awaiting_account_id"},
    {"name": "L6: Correcting ID mid-flow", "turns": ["My ID is ACC1002", "wait no it's ACC1001"], "expect_stage": "awaiting_verification", "expect_account_id": "ACC1001"},

    # --- Category 2: Identity Verification (Strict Rules) ---
    {"name": "V1: Exact Match (Nithin)", "turns": ["ACC1001", "Nithin Jain", "4321"], "expect_verified": True},
    {"name": "V2: Case Mismatch (Should Fail)", "turns": ["ACC1001", "nithin jain", "4321"], "expect_verified": False},
    {"name": "V3: Secondary: DOB (ACC1001)", "turns": ["ACC1001", "Nithin Jain", "1990-05-14"], "expect_verified": True},
    {"name": "V4: Secondary: Pincode (ACC1001)", "turns": ["ACC1001", "Nithin Jain", "400001"], "expect_verified": True},
    {"name": "V5: Long Name (ACC1002)", "turns": ["ACC1002", "Rajarajeswari Balasubramaniam", "9876"], "expect_verified": True},
    {"name": "V6: Leap Year (ACC1004)", "turns": ["ACC1004", "Rahul Mehta", "1988-02-29"], "expect_verified": True},
    {"name": "V7: Non-Leap Year (Incorrect Rahul)", "turns": ["ACC1004", "Rahul Mehta", "1989-02-29"], "expect_verified": False},
    {"name": "V8: Partial Info Guidance", "turns": ["ACC1001", "Nithin Jain"], "expect_verified": False, "expect_stage": "awaiting_verification"},
    {"name": "V9: One-Shot (ID + Name + Aadhaar)", "turns": ["ACC1001, Nithin Jain, 4321"], "expect_verified": True},
    {"name": "V10: Mixed turns (Name then Aadhaar)", "turns": ["ACC1001", "My name is Nithin Jain", "ending in 4321"], "expect_verified": True},

    # --- Category 3: Retries & Safety ---
    {"name": "S1: Verification Lockout (3 fails)", "turns": ["ACC1001", "Wrong 1, 0000", "Wrong 2, 0000", "Wrong 3, 0000"], "expect_closed": True},
    {"name": "S2: Irrelevant Response Retry", "turns": ["ACC1001", "I am a robot", "Beep boop", "010101"], "expect_closed": True},
    {"name": "S3: Cancellation Intent", "turns": ["ACC1001", "please exit now"], "expect_closed": True},

    # --- Category 4: Memory & Change of Mind ---
    {"name": "M1: Change Name mid-verification", "turns": ["ACC1001", "Incorrect Name", "Wait, I want to change the name", "Nithin Jain", "4321"], "expect_verified": True},
    {"name": "M2: Change ID after verification start", "turns": ["ACC1002", "I mean ACC1001", "Nithin Jain", "4321"], "expect_verified": True},

    # --- Category 5: Payment Amount (Parsing & Logic) ---
    {"name": "A1: Word amount", "turns": ["ACC1001", "Nithin Jain", "4321", "five hundred"], "expect_amount": Decimal("500.00")},
    {"name": "A2: Currency symbol", "turns": ["ACC1001", "Nithin Jain", "4321", "₹100.50"], "expect_amount": Decimal("100.50")},
    {"name": "A3: Full Balance keyword", "turns": ["ACC1001", "Nithin Jain", "4321", "pay the full amount"], "expect_amount": Decimal("1250.75")},
    {"name": "A4: Over Balance (Should fail)", "turns": ["ACC1001", "Nithin Jain", "4321", "2000"], "expect_amount": None},
    {"name": "A5: Zero Balance (Priya)", "turns": ["ACC1003", "Priya Agarwal", "2468"], "expect_verified": True, "expect_stage": "verified"},
    {"name": "A6: Partial Payment", "turns": ["ACC1001", "Nithin Jain", "4321", "10"], "expect_amount": Decimal("10.00")},
    {"name": "A7: Invalid decimals (3 places)", "turns": ["ACC1001", "Nithin Jain", "4321", "100.555"], "expect_amount": None},

    # --- Category 6: Card Details (Formatting & Luhn) ---
    {"name": "C1: Spaced Card Number", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532 0151 1283 0366", "123", "12/2027"], "expect_payment_success": True},
    {"name": "C2: Masked Number (Should Fail)", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532****0366", "123", "12/2027"], "expect_payment_success": False},
    {"name": "C3: Luhn Failure", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532015112830367", "123", "12/2027"], "expect_payment_success": False},
    {"name": "C4: Amex CVV (4 digits)", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "340000000000000", "1234", "12/2027"], "expect_payment_success": False},
    {"name": "C5: Past Expiry", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532015112830366", "123", "01/2020"], "expect_payment_success": False},
    {"name": "C6: Written Expiry", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532015112830366", "123", "December 2027"], "expect_payment_success": True},

    # --- Category 7: API Error Codes & Service States ---
    {"name": "E1: Insufficient Balance API", "turns": ["ACC1001", "Nithin Jain", "4321", "1250.75", "Nithin Jain", "4532015112830366", "123", "12/2027"], "expect_payment_success": True},
    {"name": "E2: Multiple Turns for Card", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "My card is 4532015112830366", "CVV is 123", "Expiry is 12/27", "Name is Nithin"], "expect_payment_success": True},
    {"name": "E3: Invalid CVV Length", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532015112830366", "12", "12/27"], "expect_payment_success": False},
    {"name": "E4: Masked CVV (Words)", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532015112830366", "CVV is one two three", "12/27"], "expect_payment_success": True},

    # --- Category 8: Advanced Conversational Flow ---
    {"name": "F1: Greetings and Help", "turns": ["Hello", "Can you help me pay?", "My ID is ACC1001"], "expect_stage": "awaiting_verification"},
    {"name": "F2: Interrupt with Balance Question", "turns": ["ACC1001", "What is my balance?", "Nithin Jain", "4321"], "expect_verified": True},
    {"name": "F3: Out of Order Card Name", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "use card for Nithin Jain", "4532015112830366", "123", "12/27"], "expect_payment_success": True},
    {"name": "F4: Natural amount change", "turns": ["ACC1001", "Nithin Jain", "4321", "pay 1000", "wait make it 500 instead"], "expect_amount": Decimal("500.00")},
    {"name": "F5: Messy Pincode (Mixed)", "turns": ["ACC1001", "Nithin Jain", "my pincode is 40 and then 0001"], "expect_verified": True},

    # --- Category 9: Robustness ---
    {"name": "R1: Long user message", "turns": ["i think my account number is ACC1001 and my name is Nithin Jain and i was born in may 1990"], "expect_verified": True},
    {"name": "R2: Asking about security", "turns": ["ACC1001", "is this safe?", "Nithin Jain", "4321"], "expect_verified": True},
    {"name": "R3: Complex Expiry (YY)", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532015112830366", "123", "12/27"], "expect_payment_success": True},
    {"name": "R4: Complex Expiry (MM/YY)", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532015112830366", "123", "12 / 27"], "expect_payment_success": True},
    {"name": "R5: Final Closing Turn", "turns": ["ACC1001", "Nithin Jain", "4321", "500", "Nithin Jain", "4532015112830366", "123", "12/27", "Thanks, bye!"], "expect_closed": True},
]

from tests.llm_judge import LLMJudge

# ... (TEST_CASES remains same) ...

@pytest.mark.parametrize("case", TEST_CASES)
def test_agent_scenarios(case):
    """
    Automated scenario testing with real LLM calls and an AI Judge.
    """
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("Skipping scenario test because OPENAI_API_KEY is not set.")

    agent = Agent()
    transcript = ""
    for turn in case['turns']:
        transcript += f"USER: {turn}\n"
        res = agent.next(turn)
        transcript += f"AGENT: {res['message']}\n"

    # 1. Deterministic Checks (Safety Net)
    if "expect_verified" in case:
        assert agent.state.verified == case["expect_verified"]
    
    # 2. AI Judge Evaluation (Deep Audit)
    judge = LLMJudge()
    expected_desc = case.get("name", "Successful interaction")
    result = judge.evaluate_conversation(transcript, expected_desc)
    
    print(f"\nJudge Verdict for {case['name']}:")
    print(f"  Score: {result['score']}/10")
    print(f"  Pass: {result['pass']}")
    print(f"  Reasoning: {result['reasoning']}")

    assert result["pass"] is True, f"AI Judge failed the agent on {case['name']}: {result['reasoning']}"
    assert result["security_violation"] is False, f"SECURITY VIOLATION DETECTED in {case['name']}"

if __name__ == "__main__":
    # If run directly, behave like evaluate_agent.py but with AI Judge
    print(f"{'='*20} AI-JUDGED EVALUATION START {'='*20}")
    passed = 0
    total = len(TEST_CASES)
    judge = LLMJudge()
    
    for case in TEST_CASES:
        print(f"\nTEST: {case['name']}")
        agent = Agent()
        transcript = ""
        for turn in case['turns']:
            transcript += f"USER: {turn}\n"
            res = agent.next(turn)
            transcript += f"AGENT: {res['message']}\n"
        
        result = judge.evaluate_conversation(transcript, case['name'])
        if result['pass']:
            print(f"  PASSED [OK] (Score: {result['score']}/10)")
            passed += 1
        else:
            print(f"  FAILED: {result['reasoning']}")
    
    print(f"\n{'='*20} SUMMARY {'='*20}")
    print(f"Passed: {passed}/{total} ({passed/total*100:.1f}%)")
