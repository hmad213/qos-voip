# adaptive buffer. changes the delay based on certain measures,

# adaptive buffer. changes the delay based on certain measures
#
# Measures: RFC 3550 style interarrival jitter (smoothed with 1/16) and late packets.
#   - a late packet raises the delay by one frame at once
#   - every adapt_interval seconds the delay is moved towards  jitter_multiplier * jitter
#     (rounded up to whole frames): up straight away, down only one frame per interval
# Changing the delay shifts the playout clock, so a raise leaves one slot of silence
# (the PLC fills it) and a cut makes the next frames come out a little early.
import math

from src.jitter.base import JitterBuffer, LATE


class AdaptiveJitterBuffer(JitterBuffer):
    def __init__(self, initial_delay_ms: float = 80, min_delay_ms: float = 40,
                 max_delay_ms: float = 200, jitter_multiplier: float = 4.0,
                 adapt_interval: float = 1.0):
        if not min_delay_ms <= initial_delay_ms <= max_delay_ms:
            raise ValueError("need min_delay_ms <= initial_delay_ms <= max_delay_ms")
        super().__init__(initial_delay_ms)
        self.min_delay = min_delay_ms / 1000
        self.max_delay = max_delay_ms / 1000
        self.jitter_multiplier = jitter_multiplier
        self.adapt_interval = adapt_interval

        self.jitter = 0.0                       # seconds
        self.delay_changes = []                 # (time, new delay in ms), handy for plots
        self._prev_transit = None
        self._last_adapt = None
        self._bump_pending = False

    def _observe(self, ext_seq: int, arrival_time: float) -> None:
        # transit = arrival minus the time the sender should have sent it (slot number * 20 ms)
        transit = arrival_time - (ext_seq - self._base_seq) * self.frame_time
        if self._prev_transit is not None:
            d = abs(transit - self._prev_transit)
            self.jitter += (d - self.jitter) / 16
        self._prev_transit = transit
        if self._last_adapt is None:
            self._last_adapt = arrival_time

    def push(self, packet, arrival_time):
        status = super().push(packet, arrival_time)
        if status == LATE:
            self._bump_pending = True
        return status

    def _jitter_target(self) -> float:
        wanted = self.jitter_multiplier * self.jitter
        wanted = math.ceil(wanted / self.frame_time - 1e-9) * self.frame_time
        return min(max(wanted, self.min_delay), self.max_delay)

    def _set_delay(self, new_delay: float, now: float) -> None:
        new_delay = min(max(new_delay, self.min_delay), self.max_delay)
        if abs(new_delay - self.delay) < 1e-9:
            return
        self._base_time += new_delay - self.delay
        self.delay = new_delay
        self.delay_changes.append((now, new_delay * 1000))

    def _adapt(self, now: float) -> None:
        if self._base_time is None:
            return
        if self._bump_pending:
            self._bump_pending = False
            self._set_delay(self.delay + self.frame_time, now)
            return
        if now - self._last_adapt < self.adapt_interval:
            return
        self._last_adapt = now
        wanted = self._jitter_target()
        if wanted > self.delay + 1e-9:
            self._set_delay(wanted, now)
        elif wanted < self.delay - 1e-9:
            self._set_delay(self.delay - self.frame_time, now)

    def pop(self, now: float):
        self._adapt(now)
        return super().pop(now)