import numpy as np
import pytest

from src.codec import g711
from src.config import SAMPLE_RATE, SAMPLES_PER_FRAME


def test_known_values():
    assert g711.encode(np.array([0], dtype=np.int16)) == b"\xff"
    assert g711.decode(b"\xff")[0] == 0
    assert g711.decode(b"\x00")[0] == -32124
    assert g711.decode(b"\x80")[0] == 32124


def test_frame_length_is_160_in_and_out():
    frame = np.zeros(SAMPLES_PER_FRAME, dtype=np.int16)
    data = g711.encode(frame)
    assert isinstance(data, bytes) and len(data) == 160
    out = g711.decode(data)
    assert out.dtype == np.int16 and len(out) == 160


def test_encode_accepts_a_list():
    assert g711.encode([0, 0, 0]) == b"\xff\xff\xff"


def test_round_trip_error_stays_inside_one_quantisation_step():
    x = np.arange(-32635, 32636, dtype=np.int16)
    back = g711.decode(g711.encode(x)).astype(np.int32)
    err = np.abs(x.astype(np.int32) - back)
    # step size is 8 << segment, which is at most (|x| + bias) / 16; half of it is the worst error
    assert np.all(err <= (np.abs(x.astype(np.int32)) + g711.BIAS) / 32 + 1)


def test_loud_samples_are_clipped_not_wrapped():
    x = np.array([32767, -32768], dtype=np.int16)
    back = g711.decode(g711.encode(x))
    assert back[0] > 31000 and back[1] < -31000


def test_negative_sample_flips_only_the_sign_bit():
    pos = np.arange(1, 32636, dtype=np.int16)
    a = np.frombuffer(g711.encode(pos), dtype=np.uint8)
    b = np.frombuffer(g711.encode(-pos), dtype=np.uint8)
    assert np.all((a ^ b) == 0x80)


def test_every_code_survives_decode_then_encode_except_negative_zero():
    codes = bytes(range(256))
    again = g711.encode(g711.decode(codes))
    assert [c for c, a in zip(codes, again) if c != a] == [0x7F]    # 0x7F is "negative zero"


def test_decode_is_monotonic_inside_each_sign():
    values = g711.decode(bytes(range(128, 256))).astype(int)        # positive half, 0x80 = loudest
    assert np.all(np.diff(values) <= 0)


def test_sine_wave_round_trip_is_close():
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    sine = (10000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    back = g711.decode(g711.encode(sine))
    snr = 10 * np.log10(np.sum(sine.astype(float) ** 2) / np.sum((sine - back).astype(float) ** 2))
    assert snr > 30