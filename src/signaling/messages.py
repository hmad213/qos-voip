from dataclasses import dataclass, asdict, fields
from typing import ClassVar
import math
import json

from src.config import CODEC_NAME, PACKET_TIME, RTP_PORT

class ProtocolError(Exception):
    pass

def _check_str(name: str, value)->None:
    if not isinstance(value, str):
        raise ProtocolError(f"{name} must be a string, got {type(value).__name__}")
    if not value:
        raise ProtocolError(f"{name} must not be empty")

def _check_int(name: str, value, low: int = -math.inf, high: int = math.inf)->None:
    if not isinstance(value, int):
        raise ProtocolError(f"{name} must be an integer, got {type(value).__name__}")
    if value < low or value > high:
        raise ProtocolError(f"{name} must be in range, got value {value}")



@dataclass
class _MediaClass:
    call_id: str
    codec: str = CODEC_NAME
    packet_time: int = PACKET_TIME    
    rtp_port: int = RTP_PORT

    def __post_init__(self):
        _check_str("Call ID", self.call_id)
        _check_str("Codec", self.codec)
        _check_int("Packet time", self.packet_time, low=1)
        _check_int("RTP port", self.rtp_port, low=1, high=65535)

@dataclass
class Invite(_MediaClass):
    TYPE: ClassVar[str] = "INVITE"


@dataclass
class Accept(_MediaClass):
    TYPE: ClassVar[str] = "ACCEPT"         


@dataclass
class Reject:
    TYPE: ClassVar[str] = "REJECT"
    call_id: str
    reason: str

    def __post_init__(self):
        _check_str("Call ID", self.call_id)
        _check_str("Reason", self.reason)


@dataclass
class Bye:
    TYPE: ClassVar[str] = "BYE"
    call_id: str

    def __post_init__(self):
        _check_str("Call ID", self.call_id)

_MESSAGE_TYPES = {cls.TYPE: cls for cls in (Invite, Accept, Reject, Bye)}

def encode(msg)->bytes:
    msg_dict = {"type": msg.TYPE, **asdict(msg)}
    msg_bytes = (json.dumps(msg_dict) + "\n").encode("utf-8")
    return msg_bytes

def decode(line: bytes):
    try:
        data = json.loads(line.decode())
    except Exception as e:
        raise ProtocolError(f"Invalid message: {e}")

    if(not isinstance(data, dict)):
        raise ProtocolError("Data must be a JSON object")

    type = data.get("type")
    if not isinstance(type, str) or type not in _MESSAGE_TYPES:
        raise ProtocolError(f"Unknown message type: {type}")
    message_class = _MESSAGE_TYPES[type]

    known = {f.name for f in fields(message_class)}
    kwargs = {k: v for k, v in data.items() if k in known}

    try:
        return message_class(**kwargs)
    except Exception as e:
        raise ProtocolError(f"Bad fields for {type}: {e}")
