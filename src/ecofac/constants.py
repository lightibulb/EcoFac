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

# Data-entry sentinel: a reading above this value (in kWh) is almost certainly
# a malformed CSV cell (e.g. a value typed with the wrong exponent). It is
# stored with is_valid=0 so it can never inflate a statistic. The threshold is
# deliberately astronomical (1e9 kWh/h ~ 1 TW) so legitimate large-facility
# readings cannot be flagged. Per-facility plausibility checks belong in
# ``quality.assessment``, not here.
MAX_VALID_READING_KWH = 1_000_000_000.0


# CSV upload limits. The byte limit applies to the *file*; the multipart
# envelope (boundaries, the two form fields, part headers) is allowed on top
# of it when the request body is bounded by ``BodySizeLimitMiddleware``.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 64 * 1024
