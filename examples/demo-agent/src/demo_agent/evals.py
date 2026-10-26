"""The entry point `agenttrace eval` calls (eval.yaml). It takes a dataset item's input, which is
the agent's arguments as "Add to dataset" copied them, and returns the answer to score."""

from dotenv import find_dotenv, load_dotenv

from agenttrace import instrument_anthropic
from demo_agent.agent import run

load_dotenv(find_dotenv(usecwd=True))
instrument_anthropic()  # each model call becomes an LLM span, so samples carry token usage


def eval_entry(input: dict) -> str:
    return run(input["question"]).answer
