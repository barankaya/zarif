"""Pydantic models for structured LLM outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CriticalItemModel(BaseModel):
    id: str = Field(description="Kısa, benzersiz kimlik, örn. 'C1'.")
    text: str = Field(description="Kritik maddenin özgün ifadesi, mümkün olduğunca birebir alıntı.")
    category: Literal["rule", "boundary", "acceptance_criterion", "warning"]


class CriticalItemsResult(BaseModel):
    items: list[CriticalItemModel]


class RetentionReport(BaseModel):
    retained_ids: list[str] = Field(default_factory=list)
    missing_ids: list[str] = Field(default_factory=list)
    new_claims: list[str] = Field(
        default_factory=list, description="Orijinalde olmayan, düzenlenmiş metne eklenmiş yeni iddialar."
    )
    sufficient: bool = Field(description="Tüm kritik maddeler korunmuş ve yeni iddia yoksa true.")
