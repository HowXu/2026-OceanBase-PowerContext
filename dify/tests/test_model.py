"""Provider failures must never be accepted as successful AI narration."""
import asyncio
import json
from pathlib import Path
import sys
import time

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "unit-gateway-secret-with-at-least-32-characters")
    monkeypatch.setenv("MODEL_API_KEY", "unit-model-secret")
    monkeypatch.setenv("MODEL_BASE_URL", "https://model.test/v1")
    monkeypatch.setenv("MODEL_NAME", "configured-model")
    from station7 import app
    return app


def request_payload(gateway):
    ticket = gateway.encode_ticket({"at": int(time.time())})
    return {"ticket": ticket, "cached": False, "state": {"items": ["会下雨的伞"]},
            "system": "你是车站站长。", "prompt": "请回应这次交换。", "fallback": "规则事件已确定。", "event": "交换"}


def run_narration(gateway, payload, handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway.app.state.http = client
            return await gateway.narrate(payload)
    return asyncio.run(run())


def test_success_records_real_response_metadata_and_memory_callback(gateway):
    def provider(request):
        assert str(request.url) == "https://model.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer unit-model-secret"
        body = json.loads(request.content)
        assert body["model"] == "configured-model"
        assert "thinking" not in body
        assert "unit-model-secret" not in json.dumps(body)
        return httpx.Response(200, json={"id": "chatcmpl-provider-response", "model": "returned-model",
            "choices": [{"message": {"content": "站长把抽屉轻轻合上。"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 23, "completion_tokens": 15, "total_tokens": 38}})
    result = run_narration(gateway, request_payload(gateway), provider)
    assert result["narrator"] == "model" and "会下雨的伞" in result["narration"]
    assert result["model_trace"] == {"model": "returned-model", "response_id": "chatcmpl-provider-response",
                                     "finish_reason": "stop",
                                     "usage": {"prompt_tokens": 23, "completion_tokens": 15, "total_tokens": 38}}


@pytest.mark.parametrize("status,body", [
    (401, {"error": "invalid key"}), (429, {"error": "rate limited"}),
    (500, {"error": "provider unavailable"}),
    (200, {"choices": [{"message": {"content": ""}}]}),
    (200, {"choices": [{"message": {"content": None}}]}),
    (200, {"choices": []}),
    (200, {"choices": [{"message": {"content": "还没讲完的故事"}, "finish_reason": "length"}]}),
])
def test_failed_or_empty_provider_response_is_labelled_fallback(gateway, status, body):
    result = run_narration(gateway, request_payload(gateway), lambda request: httpx.Response(status, json=body))
    assert result["narrator"] == "rules-fallback"
    assert result["narration"] == "规则事件已确定。"
    assert result["model_trace"] is None


def test_replayed_request_never_calls_model_again(gateway):
    payload = {"ticket": gateway.encode_ticket({"at": int(time.time())}), "cached": True, "answer": "已保存的回答"}
    def unexpected_request(request):
        raise AssertionError("A cached action must not make another paid model request")
    assert run_narration(gateway, payload, unexpected_request) == payload


def test_no_key_uses_rules_without_model_request(gateway, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "")
    def unexpected_request(request):
        raise AssertionError("Rule mode must not contact a model provider")
    result = run_narration(gateway, request_payload(gateway), unexpected_request)
    assert result["narrator"] == "rules" and result["model_trace"] is None


def test_official_deepseek_disables_thinking_for_short_narration(gateway, monkeypatch):
    monkeypatch.setenv("MODEL_BASE_URL", "https://api.deepseek.com/v1")
    def provider(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] == 1024
        return httpx.Response(200, json={"model": "deepseek-flash", "id": "complete-response",
            "choices": [{"message": {"content": "站长递来一张车票。"}, "finish_reason": "stop"}]})
    assert run_narration(gateway, request_payload(gateway), provider)["narrator"] == "model"
