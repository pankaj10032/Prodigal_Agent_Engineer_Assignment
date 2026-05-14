from agent import Agent
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    agent = Agent()
    print("Payment Collection Agent")
    print("Type `exit` to quit.")
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        response = agent.next(user_input)
        print(f"Agent: {response['message']}")


if __name__ == "__main__":
    main()
