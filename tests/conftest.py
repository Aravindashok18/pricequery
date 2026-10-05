import datetime
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricequery.executor import make_readonly_engine  # noqa: E402
from pricequery.nl2sql import PriceQuery  # noqa: E402
from pricequery.seed import create_demo_database  # noqa: E402

TODAY = datetime.date(2026, 10, 1)


class ScriptedLLM:
    """Stands in for a chat model: returns the queued replies in order and records every prompt."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []
        self.runnable = RunnableLambda(self._call)

    def _call(self, prompt_value):
        self.prompts.append(prompt_value.to_string())
        return AIMessage(content=self.replies.pop(0))

    @property
    def calls(self):
        return len(self.prompts)


@pytest.fixture(scope="session")
def demo_url(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "demo.db"
    url = f"sqlite:///{path}"
    create_demo_database(url)
    return url


@pytest.fixture()
def executor(demo_url):
    return make_readonly_engine(demo_url, timeout_seconds=2)


@pytest.fixture()
def make_engine(executor):
    def build(*replies, **kwargs):
        llm = ScriptedLLM(*replies)
        return PriceQuery(llm.runnable, executor, today=lambda: TODAY, **kwargs), llm

    return build
