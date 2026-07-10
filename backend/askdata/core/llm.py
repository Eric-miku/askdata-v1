"""LLMClient — thin OpenAI-compatible wrapper. Provides Complete() (single prompt) and Chat() (multi-turn with tool calling) for Qwen/DeepSeek models."""

from .config import settings


class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat completion endpoint."""

    def __init__(self, api_base=None, api_key=None, model_name=None):
        self.api_base = api_base or settings.LLM_API_BASE
        self.api_key = api_key or settings.LLM_API_KEY
        self.model_name = model_name or settings.LLM_MODEL_NAME

    def Complete(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is required for model calls")
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key, base_url=self.api_base)
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            raise RuntimeError(f"LLM call failed: {exc}") from exc

    def Chat(self, messages: list[dict], tools: list[dict] | None = None):
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is required for model calls")
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key, base_url=self.api_base)
            kwargs = {
                "model": self.model_name,
                "messages": messages,
                "temperature": 0,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message
        except Exception as exc:
            raise RuntimeError(f"LLM chat failed: {exc}") from exc
