import argparse
import sys

from dotenv import find_dotenv, load_dotenv

from demo_agent.agent import AgentError, run


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    parser = argparse.ArgumentParser(description="Ask the Keyly support agent a question.")
    parser.add_argument("question")
    args = parser.parse_args()
    try:
        result = run(args.question)
    except AgentError as e:
        sys.exit(f"agent error: {e}")
    for call in result.tool_calls:
        mark = "✗" if call.is_error else "✓"
        print(f"  {mark} {call.name}({call.args}) -> {call.result[:120]}")
    print(f"\n{result.answer}")


if __name__ == "__main__":
    main()
