# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# Unified Ollama client.
#
# Note: unlike the health/multi-agent projects, we do NOT use format=json here.
# Forcing JSON encoding around source code corrupts quote characters and
# backslashes, and code-specialized models (deepseek-coder, qwen-coder) are
# tuned to emit code directly — JSON mode hurts quality. We use plain text
# generation with code-fence stripping and AST validation downstream.
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

from __future__ import annotations

from typing import Optional

import ollama


class LLMClient:
    """Thin wrapper over ollama.chat with a single, consistent interface."""

    def __init__(self, base_url: str, default_model: str):
        self.base_url = base_url
        self.default_model = default_model
        self._client = ollama.Client(host=base_url)

    def chat_text(
        self,
        prompt: str,
        temperature: float,
        model: Optional[str] = None,
    ) -> str:
        """Single-turn chat call. Returns the assistant's text content."""
        resp = self._client.chat(
            model=model or self.default_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": temperature},
        )
        return resp["message"]["content"].strip()
