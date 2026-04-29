"""Technical indicators powered by TA-Lib.

Each indicator is registered via @register and returns a dict of named Series.
The stock page UI reads INDICATOR_REGISTRY to build the selector.
"""

# Import overlay and panel modules to trigger registration
import indicators.overlays
import indicators.panels  # noqa: F401
from indicators.registry import INDICATOR_REGISTRY, compute_indicators  # noqa: F401
