import hashlib
import json

import httpx
import pytest
from aica_runtime.deepseek import DeepSeekProvider, ProviderSettings
from aica_runtime.evidence import prepare_evidence
from aica_schema.protocol import ProtocolError, dumps
from semantic_fixture import sample_archive, sample_draft


def provider_with(handler):
    settings = ProviderSettings(model="deepseek-flash", api_key="unit-test-provider-key-not-a-real-secret")
    return DeepSeekProvider(settings, httpx.MockTransport(handler))


def test_json_request_and_only_final_answer_are_processed():
    archive, _ = sample_archive()
    prepared = prepare_evidence(archive, "import-test", hashlib.sha256(dumps(archive).encode()).hexdigest())

    def handler(request):
        body = json.loads(request.content)
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert body["model"] == "deepseek-flash" and body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"} and "tools" not in body
        assert "JSON" in body["messages"][0]["content"]
        assert "unit-test-provider-key" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": dumps(sample_draft(prepared)), "reasoning_content": "PRIVATE_THOUGHT_NOT_TO_SAVE"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    report = provider_with(handler).analyze(prepared)
    assert "PRIVATE_THOUGHT_NOT_TO_SAVE" not in dumps(report)
    assert report["engine"]["model"] == "deepseek-flash" and report["usage"]["total_tokens"] == 150


@pytest.mark.parametrize("failure", ["http", "redirect", "timeout", "invalid_json", "unfinished", "oversize"])
def test_provider_failures_do_not_retry_or_leak_remote_bodies(failure):
    archive, _ = sample_archive()
    prepared = prepare_evidence(archive, "import-test", "a" * 64)
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("unit-test-provider-key-not-a-real-secret", request=request)
        if failure in {"http", "redirect"}:
            return httpx.Response(401 if failure == "http" else 307, text="PRIVATE_REMOTE_BODY",
                                  headers={"Location": "https://unrelated.invalid"})
        if failure == "oversize":
            return httpx.Response(200, content=b"a" * (513 * 1024))
        if failure == "unfinished":
            return httpx.Response(200, json={"choices": [{"finish_reason": "length"}]})
        return httpx.Response(200, text="PRIVATE_REMOTE_BODY")

    with pytest.raises(ProtocolError) as error:
        provider_with(handler).analyze(prepared)
    assert len(calls) == 1
    assert "PRIVATE_REMOTE_BODY" not in str(error.value) and "unit-test-provider-key" not in str(error.value)
