import numpy as np
import pytest

from src.config import PACKET_TIME, SAMPLES_PER_FRAME
from src.jitter.adaptive import AdaptiveJitterBuffer
from src.jitter.base import QUEUED, LATE, DUPLICATE
from src.jitter.fixed import FixedJitterBuffer
from src.jitter.plc import LossConcealer
from src.rtp.packet import Packet

FRAME = PACKET_TIME / 1000          # 0.02 s


def make_packet(seq):
    return Packet(payload_type=0, sequence_number=seq & 0xFFFF,
                  timestamp=(seq * SAMPLES_PER_FRAME) & 0xFFFFFFFF,
                  ssrc=0x12345678, payload=bytes([seq & 0xFF]) * SAMPLES_PER_FRAME)


def simulate(buf, arrivals, duration, tick=FRAME, slots=None):
    """
    arrivals: list of (arrival_time, seq). Returns (playout results with their pop time, push statuses).
    slots: stop after this many playout slots (the real app stops at BYE; the buffer itself
    would keep reporting empty slots for ever).
    """
    arrivals = sorted(arrivals)
    out, statuses, i, k = [], [], 0, 0
    while k * tick <= duration:
        t = k * tick
        while i < len(arrivals) and arrivals[i][0] <= t:
            statuses.append(buf.push(make_packet(arrivals[i][1]), arrivals[i][0]))
            i += 1
        while slots is None or len(out) < slots:
            result = buf.pop(t)
            if result is None:
                break
            out.append((t, result))
        k += 1
    return out, statuses


def steady(n, start=0.0):
    return [(start + i * FRAME, i) for i in range(n)]


# ---------------- fixed buffer ----------------

def test_in_order_stream_plays_every_frame_in_order():
    buf = FixedJitterBuffer(delay_ms=40)
    out, statuses = simulate(buf, steady(50), duration=1.5, slots=50)
    assert statuses == [QUEUED] * 50
    assert [r.ext_seq for _, r in out] == list(range(50))
    assert all(not r.lost for _, r in out)
    assert buf.played == 50 and buf.concealed == 0


def test_nothing_comes_out_before_the_delay_has_passed():
    buf = FixedJitterBuffer(delay_ms=40)
    assert buf.pop(1.0) is None                      # nothing pushed yet
    buf.push(make_packet(0), 1.0)
    assert buf.pop(1.039) is None
    result = buf.pop(1.040)
    assert result is not None and result.ext_seq == 0 and not result.lost


def test_reordering_inside_the_window_is_repaired():
    buf = FixedJitterBuffer(delay_ms=80)
    arrivals = [(0.000, 0), (0.050, 1), (0.040, 2), (0.060, 3)]   # 2 overtakes 1
    out, statuses = simulate(buf, arrivals, duration=0.5, slots=4)
    assert statuses == [QUEUED] * 4
    assert [r.ext_seq for _, r in out] == [0, 1, 2, 3]
    assert buf.concealed == 0


def test_missing_packet_is_reported_as_a_lost_slot():
    buf = FixedJitterBuffer(delay_ms=40)
    arrivals = [a for a in steady(10) if a[1] != 3]
    out, _ = simulate(buf, arrivals, duration=0.6, slots=10)
    assert [r.ext_seq for _, r in out] == list(range(10))
    assert [r.ext_seq for _, r in out if r.lost] == [3]
    assert buf.concealed == 1 and buf.played == 9


def test_packet_after_its_slot_is_late_and_not_played():
    buf = FixedJitterBuffer(delay_ms=80)
    arrivals = steady(10)
    arrivals[2] = (0.150, 2)                         # due at 0.12, arrives at 0.15
    out, statuses = simulate(buf, arrivals, duration=0.6, slots=10)
    assert LATE in statuses and statuses.count(LATE) == 1
    assert buf.late == 1
    assert [r.ext_seq for _, r in out if r.lost] == [2]
    assert [r.ext_seq for _, r in out].count(2) == 1  # never played twice


def test_duplicate_packet_is_ignored():
    buf = FixedJitterBuffer(delay_ms=40)
    assert buf.push(make_packet(0), 0.0) == QUEUED
    assert buf.push(make_packet(1), 0.02) == QUEUED
    assert buf.push(make_packet(1), 0.021) == DUPLICATE
    assert buf.duplicates == 1 and buf.buffered == 2


def test_sequence_number_wraparound():
    buf = FixedJitterBuffer(delay_ms=40)
    seqs = [65533, 65534, 65535, 0, 1, 2]
    arrivals = [(i * FRAME, s) for i, s in enumerate(seqs)]
    out, _ = simulate(buf, arrivals, duration=0.4, slots=6)
    assert [r.ext_seq for _, r in out] == [65533 + i for i in range(6)]
    assert all(not r.lost for _, r in out)


def test_first_packet_overtaken_is_not_called_late():
    buf = FixedJitterBuffer(delay_ms=40)
    assert buf.push(make_packet(1), 0.000) == QUEUED
    assert buf.push(make_packet(0), 0.005) == QUEUED  # 0 should still be played first
    first = buf.pop(0.020)
    assert first.ext_seq == 0 and not first.lost
    second = buf.pop(0.040)
    assert second.ext_seq == 1 and not second.lost


def test_payload_comes_out_unchanged():
    buf = FixedJitterBuffer(delay_ms=40)
    buf.push(make_packet(7), 0.0)
    assert buf.pop(0.04).payload == make_packet(7).payload


def test_negative_delay_is_rejected():
    with pytest.raises(ValueError):
        FixedJitterBuffer(delay_ms=-1)


# ---------------- adaptive buffer ----------------

def test_adaptive_delay_shrinks_on_a_clean_network():
    buf = AdaptiveJitterBuffer(initial_delay_ms=120, min_delay_ms=40, max_delay_ms=200)
    out, _ = simulate(buf, steady(500), duration=10.5, slots=500)
    assert buf.delay_ms == pytest.approx(40)
    assert buf.concealed == 0                        # shrinking must not cost any frames
    assert buf.delay_changes[0][1] == pytest.approx(100)


def test_adaptive_delay_grows_when_jitter_is_high():
    buf = AdaptiveJitterBuffer(initial_delay_ms=40, min_delay_ms=40, max_delay_ms=200)
    arrivals = [(i * FRAME + (0.05 if i % 2 else 0.0), i) for i in range(500)]
    simulate(buf, arrivals, duration=10.5)
    assert buf.jitter > 0.03
    assert buf.delay_ms > 100


def test_adaptive_delay_stays_inside_its_limits():
    buf = AdaptiveJitterBuffer(initial_delay_ms=60, min_delay_ms=40, max_delay_ms=100)
    arrivals = [(i * FRAME + (0.15 if i % 3 == 0 else 0.0), i) for i in range(300)]
    simulate(buf, arrivals, duration=6.5)
    assert all(40 - 1e-6 <= d <= 100 + 1e-6 for _, d in buf.delay_changes)
    assert 40 - 1e-6 <= buf.delay_ms <= 100 + 1e-6


def test_adaptive_raises_delay_after_a_late_packet():
    buf = AdaptiveJitterBuffer(initial_delay_ms=40, min_delay_ms=40, max_delay_ms=200)
    arrivals = steady(20)
    arrivals[3] = (0.120, 3)                         # due at 0.04 + 0.06 = 0.10
    simulate(buf, arrivals, duration=0.6)
    assert buf.late == 1
    assert buf.delay_ms >= 60


def test_adaptive_rejects_bad_limits():
    with pytest.raises(ValueError):
        AdaptiveJitterBuffer(initial_delay_ms=20, min_delay_ms=40, max_delay_ms=200)


# ---------------- loss concealment ----------------

def frame(value):
    return np.full(SAMPLES_PER_FRAME, value, dtype=np.int16)


def test_plc_repeats_last_frame_then_fades_to_silence():
    plc = LossConcealer(attenuation=0.5, mute_after=3)
    plc.good(frame(8000))
    assert int(plc.conceal()[0]) == 8000             # first loss: plain repeat
    assert int(plc.conceal()[0]) == 4000
    assert int(plc.conceal()[0]) == 2000
    assert int(plc.conceal()[0]) == 0                # past mute_after
    assert plc.consecutive_losses == 4


def test_plc_resets_after_a_good_frame():
    plc = LossConcealer(attenuation=0.5, mute_after=3)
    plc.good(frame(8000))
    plc.conceal()
    plc.conceal()
    plc.good(frame(1000))
    assert plc.consecutive_losses == 0
    assert int(plc.conceal()[0]) == 1000


def test_plc_without_history_is_silence():
    out = LossConcealer().conceal()
    assert out.dtype == np.int16
    assert len(out) == SAMPLES_PER_FRAME
    assert not out.any()