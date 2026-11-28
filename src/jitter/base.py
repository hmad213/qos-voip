# contains the base class for the buffer
# contains the base class for the buffer
#
# Packets are pushed in the order they arrive and popped on the playout clock.
# Slot n (the n-th 20 ms frame) is due at
#     _base_time + (n - _base_seq) * frame_time
# where _base_time is the arrival time of the first packet plus the buffer delay.
# pop(now) hands back one Slot per call once it is due: the payload if the packet arrived
# in time, otherwise lost=True (the PLC fills that slot). After the stream has ended pop()
# keeps reporting empty slots, so the caller has to stop at BYE.
#
# Subclasses: FixedJitterBuffer (constant delay) and AdaptiveJitterBuffer (uses _observe).
from dataclasses import dataclass
from typing import Optional

from src.config import PACKET_TIME

QUEUED = "queued"
LATE = "late"
DUPLICATE = "duplicate"

SEQ_MOD = 1 << 16
SEEN_WINDOW = 512               # played slots remembered, to tell a duplicate from a late packet
EPS = 1e-9


@dataclass
class Slot:
    ext_seq: int
    lost: bool
    payload: Optional[bytes] = None


class JitterBuffer:
    def __init__(self, delay_ms: float = 80):
        if delay_ms < 0:
            raise ValueError(f"delay_ms must not be negative, got {delay_ms}")
        self.frame_time = PACKET_TIME / 1000            # seconds
        self.delay = delay_ms / 1000                    # seconds

        self._base_seq = None                           # extended sequence number of the first slot
        self._base_time = None                          # playout time of that slot
        self._next = None                               # next slot to hand out
        self._max_ext = None                            # highest extended sequence number seen
        self._started = False                           # True once the first slot has been popped
        self._slots = {}                                # ext_seq -> payload
        self._seen = set()

        self.played = 0                                 # slots played from a real packet
        self.concealed = 0                              # slots handed out empty (lost)
        self.late = 0                                   # arrived after their slot was gone
        self.duplicates = 0

    # ---- numbers ----
    @property
    def delay_ms(self) -> float:
        return self.delay * 1000

    @property
    def buffered(self) -> int:
        return len(self._slots)

    @property
    def highest_ext(self) -> Optional[int]:
        return self._max_ext

    @property
    def next_ext(self) -> Optional[int]:
        return self._next

    def _playout_time(self, ext_seq: int) -> float:
        return self._base_time + (ext_seq - self._base_seq) * self.frame_time

    def next_due(self) -> Optional[float]:
        # time the next slot is due, None until the first packet has arrived
        if self._base_time is None:
            return None
        return self._playout_time(self._next)

    # ---- sequence number unwrapping ----
    def _extend(self, seq: int) -> int:
        if self._max_ext is None:
            self._max_ext = seq
            return seq
        delta = (seq - (self._max_ext & 0xFFFF)) & 0xFFFF
        if delta < 0x8000:
            self._max_ext += delta
            return self._max_ext
        return self._max_ext - (SEQ_MOD - delta)

    def _observe(self, ext_seq: int, arrival_time: float) -> None:
        # hook for the adaptive buffer: called for every packet that is not a duplicate
        pass

    # ---- in and out ----
    def push(self, packet, arrival_time: float) -> str:
        ext = self._extend(packet.sequence_number)
        if ext in self._seen:
            self.duplicates += 1
            return DUPLICATE

        if self._base_seq is None:
            self._base_seq = self._next = ext
            self._base_time = arrival_time + self.delay
        elif not self._started and ext < self._base_seq:
            # an earlier packet overtook the first one before playout began: it goes first
            self._base_time -= (self._base_seq - ext) * self.frame_time
            self._base_seq = self._next = ext

        self._seen.add(ext)
        self._observe(ext, arrival_time)

        if ext < self._next:                            # its slot was already played or skipped
            self.late += 1
            return LATE
        self._slots[ext] = packet.payload
        return QUEUED

    def pop(self, now: float) -> Optional[Slot]:
        if self._base_time is None or now < self._playout_time(self._next) - EPS:
            return None
        ext = self._next
        self._next += 1
        self._started = True
        self._seen.discard(ext - SEEN_WINDOW)

        payload = self._slots.pop(ext, None)
        if payload is None:
            self.concealed += 1
            return Slot(ext, True)
        self.played += 1
        return Slot(ext, False, payload)