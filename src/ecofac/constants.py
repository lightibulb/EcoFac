# Copyright 2026 Lightibulb

"""Shared constants for detection, quality, and screening."""

# Detection window (in readings)
DETECTION_WINDOW_SIZE = 24
DEFAULT_FREQUENCY_MINUTES = 60

# Rolling baseline detector
ROLLING_Z_THRESHOLD = 3.0
ROLLING_Z_HIGH = 4.5

# Robust MAD detector
MAD_Z_THRESHOLD = 3.5
MAD_Z_HIGH = 5.0
ZERO_MAD_EPSILON = 1e-6
MAD_SCALE = 1.4826

# Comparable-period confirmation
COMPARABLE_LOOKBACK_DAYS = 28
COMPARABLE_SAMPLES = 5
COMPARABLE_MIN_SAMPLES = 3
COMPARABLE_Z_THRESHOLD = 3.5
COMPARABLE_REL_FLOOR = 0.05

# Batch screening margin
SCREEN_MARGIN = 0.98

# Numerical sanity bound: a single reading above this value (in kWh)
# is physically impossible; it is stored but marked is_valid=0.
MAX_VALID_READING_KWH = 1_000_000_000.0
