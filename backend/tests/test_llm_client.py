from ai import llm_client


def test_groq_provider_uses_openai_compatible_chat_completion(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "Hello!"}}]}

    def fake_post(url, *, headers, json, timeout):
        captured.update({
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        return FakeResponse()

    monkeypatch.setattr(llm_client, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(llm_client, "GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr(llm_client, "GROQ_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    result = llm_client._generate_with_groq(
        [{"role": "user", "content": "Hi"}],
        system="Be brief.",
        model=None,
        max_tokens=32,
        temperature=0.2,
    )

    assert result == "Hello!"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "openai/gpt-oss-20b"
    assert captured["json"]["messages"][0] == {"role": "system", "content": "Be brief."}
    assert captured["timeout"] == 10
