from types import SimpleNamespace

import openai

from askdata.core.llm import LLMClient


class FakeCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        message = SimpleNamespace(content="OK", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAI:
    instances = []

    def __init__(self, **kwargs):
        self.options = kwargs
        self.chat = SimpleNamespace(completions=FakeCompletions())
        self.__class__.instances.append(self)


def install_fake_client(monkeypatch):
    FakeOpenAI.instances = []
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)


def test_complete_sends_deepseek_thinking_options(monkeypatch):
    install_fake_client(monkeypatch)
    client = LLMClient(
        api_base="https://api.deepseek.com",
        api_key="test-key",
        model_name="deepseek-v4-pro",
        thinking_enabled=True,
        reasoning_effort="high",
    )

    assert client.Complete("Hello") == "OK"

    instance = FakeOpenAI.instances[0]
    request = instance.chat.completions.requests[0]
    assert instance.options == {
        "api_key": "test-key",
        "base_url": "https://api.deepseek.com",
    }
    assert request["model"] == "deepseek-v4-pro"
    assert request["reasoning_effort"] == "high"
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in request


def test_chat_preserves_tools_with_thinking_enabled(monkeypatch):
    install_fake_client(monkeypatch)
    client = LLMClient(
        api_key="test-key",
        thinking_enabled=True,
        reasoning_effort="high",
    )
    tools = [{"type": "function", "function": {"name": "run_query"}}]

    message = client.Chat([{"role": "user", "content": "Hello"}], tools=tools)

    request = FakeOpenAI.instances[0].chat.completions.requests[0]
    assert message.content == "OK"
    assert request["tools"] == tools
    assert request["tool_choice"] == "auto"
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}


def test_non_thinking_mode_keeps_deterministic_temperature(monkeypatch):
    install_fake_client(monkeypatch)
    client = LLMClient(api_key="test-key", thinking_enabled=False)

    client.Complete("Hello")

    request = FakeOpenAI.instances[0].chat.completions.requests[0]
    assert request["temperature"] == 0
    assert "reasoning_effort" not in request
    assert "extra_body" not in request
