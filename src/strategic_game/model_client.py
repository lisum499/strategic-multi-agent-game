"""Minimal client for an OpenAI-compatible local chat endpoint."""

from openai import OpenAI


class OpenAICompatibleLanguageModel:

  def __init__(
      self, model_name: str, base_url: str, api_key: str = "local"
  ) -> None:
    self._model_name = model_name
    self._client = OpenAI(api_key=api_key, base_url=base_url)

  def sample_text(
      self,
      prompt: str,
      *,
      max_tokens: int = 1400,
      temperature: float = 0.4,
      top_p: float = 0.9,
      seed: int | None = None,
  ) -> str:
    response = self._client.chat.completions.create(
        model=self._model_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是国际战略模拟中的国家决策者。严格遵守规则，"
                    "只输出要求的JSON。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
    )
    return (response.choices[0].message.content or "").strip()
