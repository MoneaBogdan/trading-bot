"""Strategy base class and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from btypes import Bar, Signal, StrategyState


class Strategy(ABC):
    name: str = "unnamed"
    lookback: int = 100  # how many recent bars to keep in StrategyState
    min_adx: float | None = None  # if set, strategy only trades when ADX >= this

    # --- Tunable parameters ---
    # Strategies override `param_grid()` to expose hyperparameters for the
    # walk-forward tuner. Each grid entry is `{name: [list of candidate values]}`.
    # The tuner instantiates the strategy and calls `.set_params(**combo)` for
    # each combination on the train window, then evaluates the winner on test.
    # An empty grid means "no tuning" — walk-forward degenerates to stability mode.

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {}

    def set_params(self, **kwargs) -> None:
        """Apply a parameter override. Default impl sets attributes by name."""
        for k, v in kwargs.items():
            if not hasattr(self, k):
                raise AttributeError(f"{type(self).__name__} has no parameter {k!r}")
            setattr(self, k, v)

    def current_params(self) -> dict:
        """Return current values of all tunable params (for logging)."""
        return {k: getattr(self, k) for k in self.param_grid().keys()}

    def on_start(self, state: StrategyState) -> None:
        """Called once before the first bar. Initialize state.memory here."""

    def passes_filters(self, state: StrategyState) -> bool:
        """Run any pre-signal gates. Strategies call this first thing in on_bar()."""
        if self.min_adx is None:
            return True
        from indicators import adx_passes
        return adx_passes(state.recent_bars, self.min_adx)

    @abstractmethod
    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        """Return a Signal to open/close a position, or None to do nothing.

        Conventions:
          - Returning a Signal when no position is open opens one.
          - Returning a Signal with side opposite to current position closes
            the existing position and opens the new one (reversal).
          - To close without reversing, set position to None via a signal_close
            (not yet supported — currently you wait for stop or use TP).
        """


# --- registry ---

_REGISTRY: dict[str, Callable[[], Strategy]] = {}


def register(factory: Callable[[], Strategy]) -> Callable[[], Strategy]:
    instance = factory()
    _REGISTRY[instance.name] = factory
    return factory


def get(name: str) -> Strategy:
    if name not in _REGISTRY:
        raise KeyError(f"strategy {name!r} not registered. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def available() -> list[str]:
    return sorted(_REGISTRY)
