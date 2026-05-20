"""
LLM 호출 어댑터.

기본값은 Anthropic Claude API이고, LLM_PROVIDER=ollama 로 설정하면
로컬 Ollama 모델을 사용한다. Ollama는 API 키가 필요 없지만 로컬에
Ollama 서버와 모델이 먼저 준비되어 있어야 한다.
"""

import requests
from typing import Optional

from shared.settings import (
    ANTHROPIC_API_KEY,
    LLM_PROVIDER,
    MODELS,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)


def generate_text(
    messages: list[dict],
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 300,
    temperature: float = 0.2,
) -> str:
    if LLM_PROVIDER == "ollama":
        return _generate_with_ollama(
            messages,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    return _generate_with_anthropic(
        messages,
        system=system,
        model=model,
        max_tokens=max_tokens,
    )


def _generate_with_anthropic(
    messages: list[dict],
    *,
    system: Optional[str],
    model: Optional[str],
    max_tokens: int,
) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic 패키지가 없습니다. Claude API를 쓰려면 `pip install -r requirements.txt`를 실행하거나, "
            "로컬 Ollama를 쓰려면 `.env`에서 `LLM_PROVIDER=ollama`로 설정하세요."
        ) from exc

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    kwargs = {
        "model": model or MODELS.judge_model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    msg = client.messages.create(**kwargs)
    return msg.content[0].text.strip()


def _generate_with_ollama(
    messages: list[dict],
    *,
    system: Optional[str],
    model: Optional[str],
    max_tokens: int,
    temperature: float,
) -> str:
    ollama_messages = []
    if system:
        ollama_messages.append({"role": "system", "content": system})
    ollama_messages.extend(messages)

    resp = requests.post(
        f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat",
        json={
            "model": model or OLLAMA_MODEL,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()
