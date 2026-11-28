# provides a receiver report

# provides a receiver report. RFC 3550 receiver report (PT 201, one report block, 32 bytes)
# sent over UDP on RTP_PORT + 1, so Wireshark decodes it as RTCP. LSR/DLSR are left at 0
# (there are no sender reports, so no round trip time).
import asyncio
import struct
import time
from dataclasses import dataclass
from typing import Callable, Optional

from src.config import RTP_PORT, RTP_VERSION
from src.metrics.stats import StreamStats

RTCP_PORT = RTP_PORT + 1
RTCP_INTERVAL = 1.0             # seconds between reports
RTCP_RR = 201

HEADER_FORMAT = "!BBHI"         # V/P/RC, packet type, length in words minus one, reporter SSRC
BLOCK_FORMAT = "!IIIIII"        # source SSRC, fraction+cumulative lost, highest seq, jitter, LSR, DLSR
HEADER_SIZE = 8
REPORT_SIZE = 32


class RtcpError(Exception):
    pass


@dataclass
class ReceiverReport:
    reporter_ssrc: int
    source_ssrc: int
    fraction_lost: int
    cumulative_lost: int
    highest_seq: int
    jitter: int
    lsr: int = 0
    dlsr: int = 0


def pack(report: ReceiverReport) -> bytes:
    b0 = (RTP_VERSION << 6) | 1                         # no padding, one report block
    length = REPORT_SIZE // 4 - 1
    header = struct.pack(HEADER_FORMAT, b0, RTCP_RR, length, report.reporter_ssrc & 0xFFFFFFFF)

    cumulative = max(-0x800000, min(report.cumulative_lost, 0x7FFFFF))
    lost_word = ((report.fraction_lost & 0xFF) << 24) | (cumulative & 0xFFFFFF)
    block = struct.pack(BLOCK_FORMAT, report.source_ssrc & 0xFFFFFFFF, lost_word,
                        report.highest_seq & 0xFFFFFFFF, report.jitter & 0xFFFFFFFF,
                        report.lsr & 0xFFFFFFFF, report.dlsr & 0xFFFFFFFF)
    return header + block


def unpack(data: bytes) -> ReceiverReport:
    if len(data) < REPORT_SIZE:
        raise RtcpError(f"Data is too short: {len(data)} bytes")

    b0, packet_type, _, reporter_ssrc = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    if b0 >> 6 != RTP_VERSION:
        raise RtcpError(f"RTCP version not supported: {b0 >> 6}")
    if packet_type != RTCP_RR:
        raise RtcpError(f"Not a receiver report: packet type {packet_type}")
    if b0 & 0x1F != 1:
        raise RtcpError(f"Expected one report block, got {b0 & 0x1F}")

    source_ssrc, lost_word, highest_seq, jitter, lsr, dlsr = struct.unpack(
        BLOCK_FORMAT, data[HEADER_SIZE:REPORT_SIZE])
    cumulative = lost_word & 0xFFFFFF
    if cumulative & 0x800000:                           # 24 bit signed
        cumulative -= 0x1000000
    return ReceiverReport(reporter_ssrc, source_ssrc, lost_word >> 24, cumulative,
                          highest_seq, jitter, lsr, dlsr)


class ReceiverReporter:
    """Callee side. Run run() as a task next to the RTP receiver, cancel it at BYE."""

    def __init__(self, stats: StreamStats, reporter_ssrc: int, source_ssrc: int,
                 peer_ip: str, peer_port: int = RTCP_PORT, interval: float = RTCP_INTERVAL):
        self.stats = stats
        self.reporter_ssrc = reporter_ssrc
        self.source_ssrc = source_ssrc
        self.peer = (peer_ip, peer_port)
        self.interval = interval
        self.sent = 0

    def build(self) -> ReceiverReport:
        r = self.stats.interval_report()
        return ReceiverReport(self.reporter_ssrc, self.source_ssrc, r.fraction_lost,
                              r.cumulative_lost, r.ext_highest_seq, r.jitter)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=self.peer)
        try:
            while True:
                await asyncio.sleep(self.interval)
                transport.sendto(pack(self.build()))
                self.sent += 1
        finally:
            if self.stats.received:                     # one last report when the call ends
                transport.sendto(pack(self.build()))
                self.sent += 1
            transport.close()


class _ReportProtocol(asyncio.DatagramProtocol):
    def __init__(self, listener):
        self.listener = listener

    def datagram_received(self, data, addr):
        try:
            report = unpack(data)
        except RtcpError:
            return
        self.listener._add(report)


class RtcpListener:
    """Caller side. Collects the receiver reports; call close() at the end of the call."""

    def __init__(self, host: str = "0.0.0.0", port: int = RTCP_PORT,
                 on_report: Optional[Callable[[ReceiverReport], None]] = None):
        self.host = host
        self.port = port
        self.on_report = on_report
        self.reports = []                               # (time.time(), ReceiverReport)
        self._transport = None

    @property
    def latest(self) -> Optional[ReceiverReport]:
        return self.reports[-1][1] if self.reports else None

    def _add(self, report: ReceiverReport) -> None:
        self.reports.append((time.time(), report))
        if self.on_report:
            self.on_report(report)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _ReportProtocol(self), local_addr=(self.host, self.port))

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


async def _demo():
    stats = StreamStats()
    for i in list(range(0, 5)) + list(range(7, 20)):    # sequence numbers 5 and 6 are lost
        stats.on_packet(i, i * 160, 100.0 + i * 0.02)

    listener = RtcpListener(host="127.0.0.1")
    await listener.start()
    reporter = ReceiverReporter(stats, reporter_ssrc=0xAAAA0001, source_ssrc=0x12345678,
                                peer_ip="127.0.0.1", interval=0.2)
    task = asyncio.create_task(reporter.run())
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0.1)
    listener.close()

    print("reports received:", len(listener.reports))
    print(listener.latest)


if __name__ == "__main__":
    asyncio.run(_demo())