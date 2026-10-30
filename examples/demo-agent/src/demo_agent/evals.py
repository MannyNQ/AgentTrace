"""The entry point `trayce eval` calls (eval.yaml). It takes a dataset item's input, which is
the agent's arguments as "Add to dataset" copied them, and returns the answer to score."""

from dotenv import find_dotenv, load_dotenv

from demo_agent.agent import run
from trayce import instrument_anthropic

load_dotenv(find_dotenv(usecwd=True))
instrument_anthropic()  # each model call becomes an LLM span, so samples carry token usage


def eval_entry(input: dict) -> str:
    return run(input["question"]).answer
