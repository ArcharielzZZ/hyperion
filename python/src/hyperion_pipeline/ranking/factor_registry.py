"""Factor registry — pluggable named factors (callables or injected raw values)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


RawFactorFn = Callable[[], float]


@dataclass(slots=True)
class FactorRegistry:
    """
    Registers optional callables for dynamic factors.

    For batch ranking, prefer passing precomputed ``raw_by_wallet`` dicts into
    ``RankingEngine``; registry is used when factors need code hooks.
    """

    _dynamic: dict[str, RawFactorFn] = field(default_factory=dict)

    def register(self, name: str, fn: RawFactorFn) -> None:
        self._dynamic[name] = fn

    def resolve(self, name: str) -> RawFactorFn | None:
        return self._dynamic.get(name)

    def compute_wallet_vector(self, names: list[str], ctx: Mapping[str, float]) -> dict[str, float]:
        """Merge ``ctx`` scalars with registered callables (ctx wins on name clash)."""

        out: dict[str, float] = {}
        for n in names:
            if n in ctx:
                out[n] = float(ctx[n])
            elif n in self._dynamic:
                out[n] = float(self._dynamic[n]())
            else:
                raise KeyError(f"Missing factor {n!r} in context and registry")
        return out
