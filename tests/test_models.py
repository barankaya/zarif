"""`get_chat_model()` sağlayıcı dispatch mantığının testleri.

Gerçek `langchain-ollama`/`langchain-openai` paketleri kurulu olsun ya da
olmasın aynı şekilde çalışsın diye `sys.modules`'a sahte modüller enjekte
ediyoruz — böylece testler ağ erişimine ya da opsiyonel bağımlılıkların
gerçekten kurulu olmasına bağımlı değil.
"""

from __future__ import annotations

import sys
import types

import pytest

from zarif.models import get_chat_model


def test_unknown_provider_raises_value_error():
    with pytest.raises(ValueError, match="Bilinmeyen model sağlayıcı"):
        get_chat_model("does-not-exist:foo")


def test_ollama_missing_dependency_raises_runtime_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "langchain_ollama", None)
    with pytest.raises(RuntimeError, match=r"zarif\[local\]"):
        get_chat_model("ollama:llama3.1")


def test_openai_missing_dependency_raises_runtime_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "langchain_openai", None)
    with pytest.raises(RuntimeError, match=r"zarif\[api\]"):
        get_chat_model("openai:gpt-4o-mini")


def test_ollama_provider_constructs_chat_model_with_given_name(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    class _FakeChatOllama:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    fake_module = types.SimpleNamespace(ChatOllama=_FakeChatOllama)
    monkeypatch.setitem(sys.modules, "langchain_ollama", fake_module)

    model = get_chat_model("ollama:mistral")

    assert isinstance(model, _FakeChatOllama)
    assert calls == [{"model": "mistral", "temperature": 0.0}]


def test_bare_model_name_defaults_to_ollama_provider(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    class _FakeChatOllama:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    fake_module = types.SimpleNamespace(ChatOllama=_FakeChatOllama)
    monkeypatch.setitem(sys.modules, "langchain_ollama", fake_module)

    get_chat_model("llama3.1")

    assert calls == [{"model": "llama3.1", "temperature": 0.0}]


def test_openai_provider_constructs_chat_model_with_given_name(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    fake_module = types.SimpleNamespace(ChatOpenAI=_FakeChatOpenAI)
    monkeypatch.setitem(sys.modules, "langchain_openai", fake_module)

    model = get_chat_model("openai:gpt-4o-mini", temperature=0.5)

    assert isinstance(model, _FakeChatOpenAI)
    assert calls == [{"model": "gpt-4o-mini", "temperature": 0.5}]
