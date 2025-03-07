from __future__ import annotations
from enum import Enum
from typing import Type

from pydantic import BaseModel
from ....config.config import Config


class NodeApproach(BaseModel):
    approach: ValueNodeApproach | ContextNodeApproach
    include_context_intersection: bool

    @classmethod
    def from_config(cls: Type[NodeApproach], generation_cls) -> NodeApproach:
        from .context_string import ContextStringGeneration
        from .value_string import ValueStringGeneration

        if issubclass(generation_cls, ContextStringGeneration):
            approach = ContextNodeApproach.from_str(
                Config.tabtree.context_string_approach
            )
            include_context_intersection = (
                Config.tabtree.context_string_with_context_intersection
            )

        elif issubclass(generation_cls, ValueStringGeneration):
            approach = ValueNodeApproach.from_str(Config.tabtree.value_string_approach)
            include_context_intersection = (
                Config.tabtree.value_string_with_context_intersection
            )
        else:
            raise ValueError(f"Invalid approach: {generation_cls}")

        return cls(
            approach=approach, include_context_intersection=include_context_intersection
        )


class ContextNodeApproach(str, Enum):
    BASE = "context_base"
    TEXT = "context_text"

    @classmethod
    def from_str(cls, value: str) -> ContextNodeApproach:
        return ContextNodeApproach(value)


class ValueNodeApproach(str, Enum):
    BASE = "value_base"
    TEXT = "value_text"
    TEXT_AUGMENTED = "value_text_augmented"

    @classmethod
    def from_str(cls, value: str) -> ValueNodeApproach:
        return ValueNodeApproach(value)
