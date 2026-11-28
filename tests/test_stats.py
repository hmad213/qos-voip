import csv

import pytest

from src.config import SAMPLE_RATE, SAMPLES_PER_FRAME, PACKET_TIME
from src.metrics.logger import PacketLogger, FIELDS
from src.metrics.stats import StreamStats, send_time_from_timestamp
from src.rtp import rtcp

FRAME = PACKET_TIME / 1000          # 0.02 s


def feed(stats, seqs, start=100.0, offsets=None):
    # packet with sequence number n is sent at start + n * 20 ms and carries timestamp n * 160
    records = []
    for k, n in enumerate(seqs):
        offset = offsets[k] if offsets else 0.0
        records.append(stats.on_packet(n & 0xFFFF, (n * SAMPLES_PER_FRAME) & 0xFFFFFFFF,
                                       start + n * FRAME + offset))
    return records


def test_clean_stream_has_no_jitter_and_no_loss():
    stats = StreamStats(wallclock_timestamps=False)
    feed(stats, range(100))
    assert stats.expected == 100
    assert stats.received == 100
    assert stats.lost == 0
    assert stats.jitter_units == pytest.approx(0, abs=1e-6)


def test_rfc3550_jitter_values():
    # packet 1 is 5 ms late: D = 40 units, J = 40/16.  packet 2 is on time again: D = -40
    stats = StreamStats(wallclock_timestamps=False)
    feed(stats, [0, 1, 2], offsets=[0.0, 0.005, 0.0])
    # after the first two packets only (re-run to check the intermediate value)
    first_two = StreamStats(wallclock_timestamps=False)
    feed(first_two, [0, 1], offsets=[0.0, 0.005])
    assert first_two.jitter_units == pytest.approx(40 / 16)
    assert stats.jitter_units == pytest.approx(2.5 + (40 - 2.5) / 16)
    assert stats.jitter_ms == pytest.approx(stats.jitter_units / SAMPLE_RATE * 1000)


def test_loss_counts_and_percentage():
    stats = StreamStats(wallclock_timestamps=False)
    feed(stats, [0, 1, 2, 3, 4, 7, 8, 9])           # 5 and 6 never arrive
    assert stats.expected == 10
    assert stats.received == 8
    assert stats.lost == 2
    assert stats.loss_pct == pytest.approx(20.0)
    assert stats.lost_sequences() == [5, 6]


def test_sequence_and_timestamp_wraparound():
    stats = StreamStats(wallclock_timestamps=False)
    seqs = list(range(65530, 65530 + 12))           # crosses 65535 -> 0
    records = feed(stats, seqs)
    assert stats.expected == 12
    assert stats.lost == 0
    assert records[-1].ext_seq == 65530 + 11
    assert records[-1].seq == (65530 + 11) & 0xFFFF
    assert stats.jitter_units == pytest.approx(0, abs=1e-6)


def test_timestamp_wraparound_does_not_create_jitter():
    stats = StreamStats(wallclock_timestamps=False)
    base_ts = 0xFFFFFFFF - 3 * SAMPLES_PER_FRAME
    for i in range(10):
        stats.on_packet(i, (base_ts + i * SAMPLES_PER_FRAME) & 0xFFFFFFFF, 50.0 + i * FRAME)
    assert stats.jitter_units == pytest.approx(0, abs=1e-6)


def test_duplicate_is_counted_once():
    stats = StreamStats(wallclock_timestamps=False)
    feed(stats, [0, 1, 2])
    record = stats.on_packet(2, 2 * SAMPLES_PER_FRAME, 100.5)
    assert record.status == "duplicate"
    assert stats.duplicates == 1
    assert stats.received == 3
    assert stats.lost == 0


def test_reordering_is_not_loss():
    stats = StreamStats(wallclock_timestamps=False)
    feed(stats, [0, 1, 3, 2, 4])
    assert stats.received == 5
    assert stats.lost == 0
    assert stats.reordered == 1


def test_late_loss_and_effective_loss():
    stats = StreamStats(wallclock_timestamps=False)
    for n in range(10):
        if n == 4:
            continue                                # lost in the network
        stats.on_packet(n, n * SAMPLES_PER_FRAME, 100 + n * FRAME, late=(n in (2, 7)))
    assert stats.expected == 10
    assert stats.lost == 1
    assert stats.late == 2
    assert stats.loss_pct == pytest.approx(10.0)
    assert stats.late_loss_pct == pytest.approx(20.0)
    assert stats.effective_loss_pct == pytest.approx(30.0)


def test_one_way_delay_from_wallclock_timestamp():
    send_time = 1_700_000_000.123
    ts = int(send_time * SAMPLE_RATE) & 0xFFFFFFFF
    stats = StreamStats()
    record = stats.on_packet(0, ts, send_time + 0.035)
    assert record.owd_ms == pytest.approx(35.0, abs=0.2)
    assert record.send_time == pytest.approx(send_time, abs=0.0002)
    assert send_time_from_timestamp(ts, send_time + 0.035) == pytest.approx(send_time, abs=0.0002)


def test_one_way_delay_with_explicit_send_time():
    stats = StreamStats(wallclock_timestamps=False)
    record = stats.on_packet(0, 0, 10.050, send_time=10.0)
    assert record.owd_ms == pytest.approx(50.0)
    assert stats.summary()["owd_mean_ms"] == pytest.approx(50.0)


def test_random_timestamps_give_no_delay():
    stats = StreamStats()                           # timestamps are not wall clock
    record = stats.on_packet(0, 123456, 1_700_000_000.0)
    assert record.owd_ms is None


def test_interval_report_fraction_lost():
    stats = StreamStats(wallclock_timestamps=False)
    feed(stats, [0, 1, 2, 3, 4, 7, 8, 9])           # 2 of 10 lost
    report = stats.interval_report()
    assert report.fraction_lost == (2 << 8) // 10   # 51
    assert report.cumulative_lost == 2
    assert report.ext_highest_seq == 9
    # nothing new since the last report
    again = stats.interval_report()
    assert again.fraction_lost == 0
    assert again.cumulative_lost == 2


def test_rtcp_report_round_trip():
    report = rtcp.ReceiverReport(reporter_ssrc=0xAAAA0001, source_ssrc=0x12345678,
                                 fraction_lost=51, cumulative_lost=2,
                                 highest_seq=70000, jitter=123)
    data = rtcp.pack(report)
    assert len(data) == rtcp.REPORT_SIZE
    assert data[1] == 201
    assert rtcp.unpack(data) == report


def test_rtcp_rejects_bad_data():
    with pytest.raises(rtcp.RtcpError):
        rtcp.unpack(b"\x81\xc9")
    good = bytearray(rtcp.pack(rtcp.ReceiverReport(1, 2, 0, 0, 0, 0)))
    good[1] = 200                                   # sender report, not a receiver report
    with pytest.raises(rtcp.RtcpError):
        rtcp.unpack(bytes(good))


def test_logger_writes_rows_and_lost_packets(tmp_path):
    stats = StreamStats(wallclock_timestamps=False)
    path = tmp_path / "logs" / "packets.csv"
    with PacketLogger(str(path)) as log:
        for record in feed(stats, [0, 1, 3, 4]):
            log.log(record)
        log.log_missing(stats)

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == FIELDS
    assert [r["status"] for r in rows] == ["ok", "ok", "ok", "ok", "lost"]
    assert rows[-1]["ext_seq"] == "2"
    assert rows[0]["owd_ms"] == ""