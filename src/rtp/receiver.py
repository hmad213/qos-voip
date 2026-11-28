# Receives packet. attaches an arrival time, parses the header and hands the packet to the jitter buffer
# Receives packet. attaches an arrival time, parses the header and hands the packet to the jitter buffer
#
# Two things run side by side on the event loop:
#   - RtpReceiver.on_datagram (called by the DatagramProtocol): stamps time.time() on receipt,
#     unpacks, pushes into the jitter buffer, updates StreamStats and the CSV logger
#   - the playout task: waits for the next slot to be due, pops it, decodes (or conceals) and
#     writes 160 samples to the WAV sink
# It only plays slots up to the highest sequence number that has arrived, so the silence after
# the last packet is not counted as loss.
import asyncio
import time

from src.codec import g711
from src.config import PACKET_TIME, RTP_PAYLOAD_TYPE, RTP_PORT, SAMPLES_PER_FRAME
from src.jitter.base import JitterBuffer, LATE
from src.jitter.plc import LossConcealer
from src.metrics.stats import StreamStats
from src.rtp.packet import RtpError, unpack
from src.rtp.socket_utils import make_udp_socket

IDLE_POLL = 0.005               # seconds between checks while there is nothing to play


class _RtpProtocol(asyncio.DatagramProtocol):
    def __init__(self, receiver):
        self.receiver = receiver

    def datagram_received(self, data, addr):
        arrival_time = time.time()              # stamp first, before any parsing
        self.receiver.on_datagram(data, addr, arrival_time)

    def error_received(self, exc):
        self.receiver.socket_errors += 1


class RtpReceiver:
    """The receiver owns the sink and the logger: stop() flushes and closes both."""

    def __init__(self, jitter: JitterBuffer, stats: StreamStats = None, sink=None, logger=None,
                 concealer: LossConcealer = None, local_ip: str = "0.0.0.0",
                 local_port: int = RTP_PORT):
        self.jitter = jitter
        self.stats = stats if stats is not None else StreamStats()
        self.sink = sink
        self.logger = logger
        self.concealer = concealer if concealer is not None else LossConcealer()
        self.local_ip = local_ip
        self.local_port = local_port

        self.source_ssrc = None                 # locked to the first sender we hear
        self.peer = None                        # (ip, port) of that sender
        self.first_arrival = None
        self.last_arrival = None
        self.malformed = 0
        self.ignored = 0
        self.socket_errors = 0

        self._transport = None
        self._task = None
        self._closing = False
        self._stopped = False
        self._stop_requested = False

    # ---- network side ----
    def on_datagram(self, data: bytes, addr, arrival_time: float) -> None:
        try:
            packet = unpack(data)
        except RtpError:
            self.malformed += 1
            return
        if packet.payload_type != RTP_PAYLOAD_TYPE or len(packet.payload) != SAMPLES_PER_FRAME:
            self.ignored += 1
            return
        if self.source_ssrc is None:
            self.source_ssrc = packet.ssrc
            self.peer = addr
        elif packet.ssrc != self.source_ssrc:
            self.ignored += 1
            return

        if self.first_arrival is None:
            self.first_arrival = arrival_time
        self.last_arrival = arrival_time

        status = self.jitter.push(packet, arrival_time)
        record = self.stats.on_rtp_packet(packet, arrival_time, late=(status == LATE))
        if self.logger:
            self.logger.log(record)

    # ---- playout side ----
    def _emit(self, slot) -> None:
        if slot.lost:
            pcm = self.concealer.conceal()
        else:
            pcm = self.concealer.good(g711.decode(slot.payload))
        if self.sink:
            self.sink.write(pcm)

    async def _playout(self) -> None:
        buf = self.jitter
        while True:
            due = buf.next_due()
            if due is None or buf.next_ext > buf.highest_ext:
                if self._closing:
                    return
                await asyncio.sleep(IDLE_POLL)
                continue
            wait = due - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
                continue                        # look again: the delay may have changed
            slot = buf.pop(time.time())
            if slot is not None:
                self._emit(slot)

    # ---- life cycle ----
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        sock = make_udp_socket(self.local_ip, self.local_port, tos=0)
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _RtpProtocol(self), sock=sock)
        self._task = asyncio.create_task(self._playout())

    def request_stop(self) -> None:
        self._stop_requested = True

    async def wait_for_end(self, idle_timeout: float = 2.0, first_packet_timeout: float = None) -> None:
        # returns when no packet has arrived for idle_timeout seconds (stand-in for BYE),
        # when nothing arrives at all for first_packet_timeout, or after request_stop()
        loop = asyncio.get_running_loop()
        started = loop.time()
        while not self._stop_requested:
            await asyncio.sleep(0.1)
            if self.last_arrival is None:
                if first_packet_timeout is not None and loop.time() - started > first_packet_timeout:
                    return
            elif time.time() - self.last_arrival > idle_timeout:
                return

    async def stop(self) -> None:
        # call at BYE: plays out what is still buffered, then flushes and closes sink and logger
        if self._stopped:
            return
        self._stopped = True
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._closing = True
        if self._task is not None:
            highest, nxt = self.jitter.highest_ext, self.jitter.next_ext
            remaining = 0 if highest is None else max(highest - nxt + 1, 0)
            timeout = remaining * PACKET_TIME / 1000 + self.jitter.delay + 1.0
            try:
                await asyncio.wait_for(self._task, timeout)
            except asyncio.TimeoutError:
                pass
        if self.sink:
            self.sink.close()
        if self.logger:
            self.logger.log_missing(self.stats)
            self.logger.close()

    def summary(self) -> dict:
        s = self.stats.summary()
        s.update({
            "played": self.jitter.played,
            "concealed": self.jitter.concealed,
            "final_delay_ms": round(self.jitter.delay_ms, 1),
            "malformed": self.malformed,
            "ignored": self.ignored,
        })
        return s