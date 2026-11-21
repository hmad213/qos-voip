# packs and unpacks data into a packet
import struct
from dataclasses import dataclass

from src.config import RTP_VERSION

HEADER_SIZE = 12
HEADER_FORMAT = "!BBHII"

class RtpError(Exception):
    pass

@dataclass
class Packet:
    payload_type: int
    sequence_number: int
    timestamp: int
    ssrc: int
    payload: bytes = b""
    marker: bool = False

def pack(packet: Packet) -> bytes:
    if not 0 <= packet.payload_type <= 127:
        raise RtpError(f"Payload type not within range: {packet.payload_type}")

    b0 = RTP_VERSION << 6

    b1 = packet.payload_type
    if packet.marker:
        b1 = b1 | 0x80

    sequence_number = packet.sequence_number & 0xFFFF
    timestamp = packet.timestamp & 0xFFFFFFFF
    ssrc = packet.ssrc & 0xFFFFFFFF

    header = struct.pack(HEADER_FORMAT, b0, b1, sequence_number, timestamp, ssrc)
    return header + packet.payload

def unpack(data: bytes) -> Packet:
    if(len(data) < HEADER_SIZE):
        raise RtpError(f"Data is too short: {len(data)} bytes")

    b0, b1, sequence_number, timestamp, ssrc = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])

    version = b0 >> 6
    if version != RTP_VERSION:
        raise RtpError(f"RTP version not supported: {version}")

    return Packet(
        payload_type=b1 & 0x7F,
        sequence_number=sequence_number,
        timestamp=timestamp,
        ssrc=ssrc,
        payload=data[HEADER_SIZE:],
        marker=bool(b1 >> 7),
    )

if __name__ == "__main__":
    pkt = Packet(payload_type=0, sequence_number=1, timestamp=160,
                    ssrc=0x12345678, payload=b"\xff" * 160, marker=True)
    raw = pack(pkt)
    print(raw[:12].hex(" "))
    print(unpack(raw) == pkt)