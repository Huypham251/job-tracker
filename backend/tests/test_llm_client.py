import anthropic
import httpx2

from app.core.config import settings
from app.llm.client import AnthropicExtractor, LLMExtractionError
from app.llm.schemas import EmailExtraction


class _FakeMessages:
    def __init__(self, *, parsed_output=None, raise_error=None):
        self._parsed_output = parsed_output
        self._raise_error = raise_error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_error is not None:
            raise self._raise_error
        return type("FakeResponse", (), {"parsed_output": self._parsed_output})()


def _make_extractor(fake_messages: _FakeMessages) -> AnthropicExtractor:
    extractor = AnthropicExtractor()
    extractor._client.messages = fake_messages
    return extractor


def test_classify_and_extract_returns_parsed_output() -> None:
    expected = EmailExtraction(
        is_job_related=True,
        confidence=0.9,
        company="Acme",
        position="SWE",
        status="applied",
        status_date=None,
        reasoning=None,
    )
    extractor = _make_extractor(_FakeMessages(parsed_output=expected))

    result = extractor.classify_and_extract(subject="s", sender="f", date="d", body="b")

    assert result == expected


def test_classify_and_extract_sends_correct_request_shape() -> None:
    expected = EmailExtraction(is_job_related=False, confidence=0.99)
    fake = _FakeMessages(parsed_output=expected)
    extractor = _make_extractor(fake)

    extractor.classify_and_extract(
        subject="Newsletter", sender="news@x.com", date="Mon", body="Check out our sale"
    )

    call = fake.calls[0]
    assert call["output_format"] is EmailExtraction
    assert call["model"] == settings.llm_model
    content = call["messages"][0]["content"]
    assert "Newsletter" in content
    assert "news@x.com" in content
    assert "Check out our sale" in content


def test_classify_and_extract_raises_when_parsed_output_is_none() -> None:
    extractor = _make_extractor(_FakeMessages(parsed_output=None))

    try:
        extractor.classify_and_extract(subject="s", sender="f", date="d", body="b")
        assert False, "expected LLMExtractionError"
    except LLMExtractionError:
        pass


def test_classify_and_extract_wraps_api_errors() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(message="boom", request=request)
    extractor = _make_extractor(_FakeMessages(raise_error=error))

    try:
        extractor.classify_and_extract(subject="s", sender="f", date="d", body="b")
        assert False, "expected LLMExtractionError"
    except LLMExtractionError:
        pass
