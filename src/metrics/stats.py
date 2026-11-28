# receiver side statistics (RFC 3550): jitter, loss, late loss and one way delay
#
# One way delay needs the send time. Sender and receiver run on the same machine (network
# namespaces share the kernel clock), so the sender starts its RTP timestamp at the wall clock:
#     first_timestamp = int(time.time() * SAMPLE_RATE) & 0xFFFFFFFF
# and the receiver recovers the send time from the timestamp. Use time.time() for arrival_time.
from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.config import SAMPLE_RATE

SEQ_MOD = 1 << 16
TS_MOD = 1 << 32
MAX_PLAUSIBLE_OWD = 10.0        # seconds; anything above means the timestamps are not wall clock


def send_time_from_timestamp(rtp_timestamp: int, arrival_time: float,
                             sample_rate: int = SAMPLE_RATE) -> float:
    # the 32 bit timestamp wraps every ~6 days, so pick the wall clock value nearest to arrival_time
    diff = (arrival_time * sample_rate - rtp_timestamp) % TS_MOD
    if diff >= TS_MOD / 2:
        diff -= TS_MOD
    return arrival_time - diff / sample_rate


@dataclass
class PacketRecord:
    ext_seq: int
    seq: int
    rtp_timestamp: int
    arrival_time: float
    send_time: Optional[float]
    owd_ms: Optional[float]
    jitter_ms: float                # running RFC 3550 jitter after this packet
    status: str                     # "ok", "late" or "duplicate"
    reordered: bool = False


@dataclass
class IntervalReport:
    # the numbers an RTCP receiver report block carries
    fraction_lost: int              # 0..255, lost / expected since the last report
    cumulative_lost: int
    ext_highest_seq: int
    jitter: int                     # in timestamp units


class StreamStats:
    def __init__(self, sample_rate: int = SAMPLE_RATE, wallclock_timestamps: bool = True):
        self.sample_rate = sample_rate
        self.wallclock_timestamps = wallclock_timestamps

        self._base_seq = None
        self._max_ext = None
        self._seen = set()
        self._max_ts_ext = None
        self._first_arrival = None
        self._prev_arrival = None       # relative to the first arrival, seconds
        self._prev_ts_ext = None

        self.received = 0               # distinct packets that arrived (late ones included)
        self.duplicates = 0
        self.late = 0                   # arrived, but after the jitter buffer needed them
        self.reordered = 0
        self.jitter_units = 0.0         # RFC 3550 interarrival jitter, timestamp units
        self.owd_ms = []

        self._expected_prior = 0
        self._received_prior = 0

    # ---- sequence number / timestamp unwrapping ----
    def _extend_seq(self, seq: int):
        if self._max_ext is None:
            self._max_ext = self._base_seq = seq
            return seq, False
        delta = (seq - (self._max_ext & 0xFFFF)) & 0xFFFF
        if delta < 0x8000:
            self._max_ext += delta
            return self._max_ext, False
        return self._max_ext - (SEQ_MOD - delta), True

    def _extend_ts(self, ts: int) -> int:
        if self._max_ts_ext is None:
            self._max_ts_ext = ts
            return ts
        delta = (ts - (self._max_ts_ext % TS_MOD)) % TS_MOD
        if delta < TS_MOD // 2:
            self._max_ts_ext += delta
            return self._max_ts_ext
        return self._max_ts_ext - (TS_MOD - delta)

    # ---- per packet ----
    def on_rtp_packet(self, packet, arrival_time: float, late: bool = False) -> PacketRecord:
        return self.on_packet(packet.sequence_number, packet.timestamp, arrival_time, late)

    def on_packet(self, seq: int, rtp_timestamp: int, arrival_time: float,
                  late: bool = False, send_time: Optional[float] = None) -> PacketRecord:
        """
        late: True when the jitter buffer rejected the packet as late (push() returned LATE).
        send_time: real send time if you have it, otherwise it is read from the timestamp.
        """
        ext, reordered = self._extend_seq(seq)
        ts_ext = self._extend_ts(rtp_timestamp)

        if ext in self._seen:
            self.duplicates += 1
            return PacketRecord(ext, seq, rtp_timestamp, arrival_time, None, None,
                                self.jitter_ms, "duplicate")

        self._seen.add(ext)
        self.received += 1
        if ext < self._base_seq:
            self._base_seq = ext
        if reordered:
            self.reordered += 1
        if late:
            self.late += 1

        # RFC 3550 A.8:  D = (Rj - Ri) - (Sj - Si),  J = J + (|D| - J) / 16
        if self._first_arrival is None:
            self._first_arrival = arrival_time
        rel = arrival_time - self._first_arrival
        if self._prev_arrival is not None:
            d = (rel - self._prev_arrival) * self.sample_rate - (ts_ext - self._prev_ts_ext)
            self.jitter_units += (abs(d) - self.jitter_units) / 16
        self._prev_arrival = rel
        self._prev_ts_ext = ts_ext

        owd = None
        if send_time is not None:
            owd = arrival_time - send_time
        elif self.wallclock_timestamps:
            send_time = send_time_from_timestamp(rtp_timestamp, arrival_time, self.sample_rate)
            owd = arrival_time - send_time
            if abs(owd) > MAX_PLAUSIBLE_OWD:
                send_time, owd = None, None
        owd_ms = None if owd is None else owd * 1000
        if owd_ms is not None:
            self.owd_ms.append(owd_ms)

        return PacketRecord(ext, seq, rtp_timestamp, arrival_time, send_time, owd_ms,
                            self.jitter_ms, "late" if late else "ok", reordered)

    # ---- totals ----
    @property
    def jitter_ms(self) -> float:
        return self.jitter_units / self.sample_rate * 1000

    @property
    def expected(self) -> int:
        if self._max_ext is None:
            return 0
        return self._max_ext - self._base_seq + 1

    @property
    def lost(self) -> int:
        # never arrived at all
        return max(self.expected - self.received, 0)

    def _pct(self, count: int) -> float:
        return 100.0 * count / self.expected if self.expected else 0.0

    @property
    def loss_pct(self) -> float:
        return self._pct(self.lost)

    @property
    def late_loss_pct(self) -> float:
        return self._pct(self.late)

    @property
    def effective_loss_pct(self) -> float:
        # what the listener experiences: lost in the network plus too late to play
        return self._pct(self.lost + self.late)

    def lost_sequences(self):
        # extended sequence numbers that never arrived
        if self._max_ext is None:
            return []
        return [s for s in range(self._base_seq, self._max_ext + 1) if s not in self._seen]

    def interval_report(self) -> IntervalReport:
        # RFC 3550 A.3: fraction lost since the previous call, for the RTCP receiver report
        expected_interval = self.expected - self._expected_prior
        received_interval = self.received - self._received_prior
        self._expected_prior = self.expected
        self._received_prior = self.received
        lost_interval = expected_interval - received_interval
        if expected_interval <= 0 or lost_interval <= 0:
            fraction = 0
        else:
            fraction = min((lost_interval << 8) // expected_interval, 255)
        return IntervalReport(
            fraction_lost=fraction,
            cumulative_lost=self.lost,
            ext_highest_seq=(self._max_ext or 0) & 0xFFFFFFFF,
            jitter=int(self.jitter_units),
        )

    def summary(self) -> dict:
        owd = np.array(self.owd_ms) if self.owd_ms else None
        return {
            "expected": self.expected,
            "received": self.received,
            "lost": self.lost,
            "late": self.late,
            "duplicates": self.duplicates,
            "reordered": self.reordered,
            "loss_pct": round(self.loss_pct, 3),
            "late_loss_pct": round(self.late_loss_pct, 3),
            "effective_loss_pct": round(self.effective_loss_pct, 3),
            "jitter_ms": round(self.jitter_ms, 3),
            "owd_mean_ms": None if owd is None else round(float(owd.mean()), 3),
            "owd_min_ms": None if owd is None else round(float(owd.min()), 3),
            "owd_max_ms": None if owd is None else round(float(owd.max()), 3),
            "owd_p95_ms": None if owd is None else round(float(np.percentile(owd, 95)), 3),
        }