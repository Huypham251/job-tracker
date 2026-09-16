from typing import Protocol

import anthropic

from app.core.config import settings
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.schemas import EmailExtraction


class Extractor(Protocol):
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction: ...


class LLMExtractionError(Exception):
    """The LLM call failed, or returned no usable structured output."""


class AnthropicExtractor:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction:
        try:
            response = self._client.messages.parse(
                model=settings.llm_model,
                max_tokens=1024,
                output_config={"effort": "low"},
                system=SYSTEM_PROMPT,
                output_format=EmailExtraction,
                messages=[
                    {
                        "role": "user",
                        "content": f"Subject: {subject}\nFrom: {sender}\nDate: {date}\n\n{body}",
                    }
                ],
            )
        except anthropic.APIError as exc:
            raise LLMExtractionError(f"Anthropic API call failed: {exc}") from exc

        if response.parsed_output is None:
            raise LLMExtractionError("Anthropic response did not include structured output")

        return response.parsed_output
