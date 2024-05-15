"""Miscellaneous type definitions for configzen."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from configzen.config import BaseConfig


ConfigObject = TypeVar("ConfigObject", bound="BaseConfig")
