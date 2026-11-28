# runs the sending loop. Send data according to fixed times
#
# Frame n is due at  start + n * 20 ms  (absolute deadlines). The loop sleeps until the
# deadline and then adds 20 ms to it, so a late wake-up is caught up by the next frames
# instead of pushing the whole stream back. The RTP timestamp starts at the wall clock
# (see src/metrics/stats.py), which is what lets the receiver work out the one way delay.
import asyncio
import random
import time
from typing import Iterable

import numpy as np

from src.codec import g711
from src.config import (PACKET_TIME, RTP_PAYLOAD_TYPE, RTP_PORT, SAMPLE_RATE,
                        TIMESTAMP_INCREMENT, TOS_BYTE)
from src.rtp.packet import Packet, pack
from src.rtp.socket_utils import make_udp_socket

LATE_SEND_MS = 5                # a frame sent more than this after its deadline counts as late


class RtpSender:
    def __init__(self, frames: Iterable[np.ndarray], peer_ip: str, peer_port: int = RTP_PORT,
                 local_ip: str = "0.0.0.0", local_port: int = 0, tos: int = TOS_BYTE,
                 ssrc: int = None, first_seq: int = None):
        self.frames = frames
        self.peer = (peer_ip, peer_port)
        self.local_ip = local_ip
        self.local_port = local_port
        self.tos = tos
        self.frame_time = PACKET_TIME / 1000
        self.ssrc = random.getrandbits(32) if ssrc is None else ssrc
        self.sequence_number = random.getrandbits(16) if first_seq is None else first_seq & 0xFFFF

        self.sent = 0
        self.late_sends = 0
        self.max_lag_ms = 0.0

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        sock = make_udp_socket(self.local_ip, self.local_port, self.tos)
        transport, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, sock=sock)
        try:
            timestamp = int(time.time() * SAMPLE_RATE) & 0xFFFFFFFF
            deadline = loop.time()
            for frame in self.frames:
                wait = deadline - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)

                lag_ms = (loop.time() - deadline) * 1000
                self.max_lag_ms = max(self.max_lag_ms, lag_ms)
                if lag_ms > LATE_SEND_MS:
                    self.late_sends += 1

                packet = Packet(payload_type=RTP_PAYLOAD_TYPE,
                                sequence_number=self.sequence_number,
                                timestamp=timestamp,
                                ssrc=self.ssrc,
                                payload=g711.encode(frame),
                                marker=(self.sent == 0))
                transport.sendto(pack(packet), self.peer)

                self.sent += 1
                self.sequence_number = (self.sequence_number + 1) & 0xFFFF
                timestamp = (timestamp + TIMESTAMP_INCREMENT) & 0xFFFFFFFF
                deadline += self.frame_time
        finally:
            transport.close()