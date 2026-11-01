"""The single home for sensitive-value shapes: consumers pick a profile and mask with it.

A profile change that lowers a corpus count needs a detector-loosening-approved marker (Decision 235 cl.1).
"""

from src.sensitive_values.classes import CLASSES, DetectorClass
from src.sensitive_values.engine import MaskResult, fingerprint, mask
from src.sensitive_values.profiles import MARKER_FORMAT, PROFILES

__all__ = ["CLASSES", "MARKER_FORMAT", "PROFILES", "DetectorClass", "MaskResult", "fingerprint", "mask"]
