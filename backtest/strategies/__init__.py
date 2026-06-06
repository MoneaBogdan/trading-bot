"""Importing this package registers all active strategies.

Retired (kept on disk for reference, NOT registered):
  - london_orb           — permutation test confirmed no edge (p=0.46, 54th %ile)
  - rsi_pullback         — actively worse than random (5th %ile of random PF)

Re-enable by adding back to the imports below if you want to revisit.
"""

from . import donchian  # noqa: F401
from . import macd_trend  # noqa: F401
from . import bb_volume_breakout  # noqa: F401
from . import chronos_signal  # noqa: F401
from . import coint_mean_rev  # noqa: F401
