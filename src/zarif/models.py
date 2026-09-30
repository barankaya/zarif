"""Provider-agnostic chat model factory. Model spec format: 'provider:model_name'."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

DEFAULT_LOCAL_MODEL = "llama3.1"
DEFAULT_API_MODEL = "gpt-4o-mini"


def get_chat_model(spec: str, temperature: float = 0.0) -> BaseChatModel:
    provider, sep, name = spec.partition(":")
    if not sep:
        # No provider prefix given: treat the whole string as an ollama model name.
        provider, name = "ollama", provider

    provider = provider.lower()

    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise RuntimeError(
                "Yerel model için 'langchain-ollama' paketi gerekli: pip install 'zarif[local]'"
            ) from exc
        return ChatOllama(model=name or DEFAULT_LOCAL_MODEL, temperature=temperature)

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "API modeli için 'langchain-openai' paketi gerekli: pip install 'zarif[api]'"
            ) from exc
        return ChatOpenAI(model=name or DEFAULT_API_MODEL, temperature=temperature)

    raise ValueError(f"Bilinmeyen model sağlayıcı: {provider!r} (beklenen: 'ollama' veya 'openai')")
