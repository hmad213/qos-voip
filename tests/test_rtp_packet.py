import pytest

from src.config import RTP_VERSION
from src.rtp.packet import HEADER_SIZE, Packet, RtpError, pack, unpack


def make(**kw):
    values = dict(payload_type=0, sequence_number=1, timestamp=160, ssrc=0x12345678,
                  payload=b"\xff" * 160, marker=False)
    values.update(kw)
    return Packet(**values)


def test_header_is_12_bytes_and_payload_follows():
    raw = pack(make())
    assert HEADER_SIZE == 12
    assert len(raw) == 12 + 160
    assert raw[12:] == b"\xff" * 160


def test_known_header_bytes():
    raw = pack(make(marker=True))
    assert raw[:12].hex() == "808000010000" "00a0" "12345678"
    assert raw[0] >> 6 == RTP_VERSION


def test_marker_bit_only_changes_the_top_bit_of_byte_1():
    assert pack(make(marker=False))[1] == 0x00
    assert pack(make(marker=True))[1] == 0x80
    assert pack(make(payload_type=96, marker=True))[1] == 0x80 | 96


def test_round_trip():
    pkt = make(payload_type=0, sequence_number=4242, timestamp=0xDEADBEEF, ssrc=0xCAFEBABE,
               payload=bytes(range(160)), marker=True)
    assert unpack(pack(pkt)) == pkt


def test_empty_payload_round_trip():
    pkt = make(payload=b"")
    assert unpack(pack(pkt)) == pkt


def test_sequence_number_wraps_at_16_bits():
    assert unpack(pack(make(sequence_number=65535))).sequence_number == 65535
    assert unpack(pack(make(sequence_number=65536))).sequence_number == 0
    assert unpack(pack(make(sequence_number=65537))).sequence_number == 1


def test_timestamp_wraps_at_32_bits():
    assert unpack(pack(make(timestamp=0xFFFFFFFF))).timestamp == 0xFFFFFFFF
    assert unpack(pack(make(timestamp=1 << 32))).timestamp == 0
    assert unpack(pack(make(timestamp=(1 << 32) + 160))).timestamp == 160


def test_stream_across_the_sequence_wrap():
    seqs = [unpack(pack(make(sequence_number=65530 + i))).sequence_number for i in range(12)]
    assert seqs == [65530, 65531, 65532, 65533, 65534, 65535, 0, 1, 2, 3, 4, 5]


def test_payload_type_out_of_range_is_rejected():
    for bad in (-1, 128, 255):
        with pytest.raises(RtpError):
            pack(make(payload_type=bad))


def test_short_data_is_rejected():
    with pytest.raises(RtpError):
        unpack(b"\x80\x00\x00\x01")
    with pytest.raises(RtpError):
        unpack(b"")


def test_wrong_version_is_rejected():
    raw = bytearray(pack(make()))
    raw[0] = (1 << 6)                       # version 1
    with pytest.raises(RtpError):
        unpack(bytes(raw))