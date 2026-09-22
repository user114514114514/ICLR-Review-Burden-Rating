"""Current scoring policy. Changing a coefficient requires a new SCORE_VERSION."""
from types import MappingProxyType

SCORE_VERSION = "score-v1.8"
SCORE_YEAR_MIN = 2024
SCHEMA_VERSION = "author-snapshot-v1"
NORMALIZER_VERSION = "openreview-normalizer-v1"
DEFAULT_ADAPTER_VERSION = "iclr-compatible-1-10-v1"
CONFIDENCE_BASE = 0.6
CONFIDENCE_STEP = 0.1
DEFAULT_CONFIDENCE = 3
MIN_VALID_REVIEWS = 2
RATING_MIN = 0.0
RATING_MAX = 10.0
RATING_CLIP_DELTA = 2.0
IQR_TO_SIGMA = 1.349
MIN_YEAR_SCALE = 0.5
SUBMISSION_FLOOR_BUFFER = 1.0
# h(p) = p / (1 + SEVERITY_SATURATION * p), which approaches 1/SEVERITY_SATURATION.
# Author weight stays outside h. The low-quality factor is 1 + alpha * h(p), not h(alpha * p).
SEVERITY_SATURATION = 2.0
BURDEN_PRODUCT_SCALE = 4.0
GOOD_TANH_MAX = 3.0
GOOD_TANH_SCALE = 0.5
ANNUAL_GOOD_CREDIT_CAP = 3.0
DR_BASE_COEFFICIENT = 0.1
BAD_AUTHOR_COEF = MappingProxyType({"rank1": 1.0, "rank2_4": 0.3, "rank5_plus": 0.1})
GOOD_AUTHOR_COEF = MappingProxyType({"rank1": 1.0, "rank2_4": 0.2, "rank5_plus": 0.05})
ACCEPTED_DECISIONS = frozenset({"oral", "spotlight", "poster", "accept"})
DECISIONS = ACCEPTED_DECISIONS | {"reject", "withdraw", "desk_reject", "unknown"}
WITHDRAWN_CALIBRATION_POLICY = "exclude unless technical_program_accepted is explicitly verified"
PERCENTILE_METHOD = "linear-type-7"
