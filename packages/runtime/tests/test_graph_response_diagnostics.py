import httpx
import pytest
from aica_runtime.deepseek import ProviderSettings
from aica_runtime.graph.__main__ import parse, preview
from aica_runtime.graph.provider import GraphProvider, GraphResponseError, decode_response
from aica_schema.protocol import dumps, loads
from graph_parser_fixture import model_reply
from test_graph_cli import provider_for, write_source


@pytest.mark.parametrize(("stage", "reply"), [
    ("output_truncated", {"choices": [{"finish_reason": "length", "message": {"content": '{"groups":['}}]}),
    ("proposal_json", {"choices": [{"finish_reason": "stop", "message": {"content": '{"groups":['}}]}),
    ("proposal_schema", model_reply({"groups": [], "edges": [], "variables": []})),
    ("response_envelope", {"choices": []}),
])
def test_response_failure_preserves_stage_without_input_values(stage, reply):
    with pytest.raises(GraphResponseError) as caught:
        decode_response(dumps(reply).encode(), ProviderSettings(model="deepseek-flash", api_key="test-key-only"))
    assert caught.value.diagnostic["stage"] == stage
    if stage == "proposal_schema":
        assert caught.value.diagnostic["errors"][0]["location"] == ["unresolved"]
        assert "input" not in caught.value.diagnostic["errors"][0]


def test_cli_saves_failed_completion_and_does_not_retry_or_reveal_credentials(tmp_path):
    calls, output = [], tmp_path / "run"
    key = "private-test-credential-123456"
    preview(write_source(tmp_path), output)

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {
            "content": '{"partial":"' + key, "reasoning_content": "private reasoning never saved"}}],
            "usage": {"completion_tokens": 4096}})

    provider = GraphProvider(ProviderSettings(model="deepseek-flash", api_key=key), httpx.MockTransport(handle))
    with pytest.raises(GraphResponseError):
        parse(output, True, provider)
    diagnostic = loads((output / "provider-response.json").read_text(encoding="utf-8"))
    assert diagnostic["stage"] == "output_truncated"
    assert diagnostic["usage"]["completion_tokens"] == 4096
    saved = (output / "provider-response.json").read_text(encoding="utf-8")
    assert key not in saved and "private reasoning" not in saved
    attempt = loads((output / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["error_details"][0]["stage"] == "output_truncated"
    with pytest.raises(ValueError, match="attempt"):
        parse(output, True, provider)
    assert len(calls) == 1 and not (output / "graph.json").exists()


@pytest.mark.parametrize("budget", [8192, 0, 8193])
def test_explicit_output_budget_is_recorded_and_validated_before_dispatch(tmp_path, budget):
    calls, output = [], tmp_path / "run"
    preview(write_source(tmp_path), output)
    provider = provider_for(calls)
    if budget != 8192:
        with pytest.raises(ValueError, match="max-output-tokens"):
            parse(output, True, provider, max_output_tokens=budget)
        assert not calls and not (output / "attempt.json").exists()
    else:
        result = parse(output, True, provider, max_output_tokens=budget)
        assert result["engine"]["max_tokens"] == 8192
        assert loads(calls[0].content.decode())["max_tokens"] == 8192
        assert provider.configuration().max_tokens == 4096
