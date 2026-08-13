from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PutResult:
    success: bool
    error: str


@dataclass(frozen=True, slots=True)
class LookupResult:
    found: bool
    value: dict[str, Any]
