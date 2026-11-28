# fixed buffer of 40/80/120 ms

# fixed buffer of 40/80/120 ms
from src.jitter.base import JitterBuffer

FIXED_DELAYS_MS = (40, 80, 120)


class FixedJitterBuffer(JitterBuffer):
    def __init__(self, delay_ms: float = 80):
        super().__init__(delay_ms)