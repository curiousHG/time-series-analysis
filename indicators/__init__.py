"""TA-Lib indicators. Each is registered via @register; the stock page UI reads
INDICATOR_REGISTRY to build the selector."""

# Import overlay and panel modules to trigger registration
import indicators.overlays
import indicators.panels
from indicators.registry import INDICATOR_REGISTRY, compute_indicators
