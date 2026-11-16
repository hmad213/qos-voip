# caller code. It connects, sends INVITE, waits for ACCEPT, starts sending data, sends BYE at end
import asyncio
import uuid
from src.config import (
    TCP_PORT, TIME_OUT, CALLEE_ADDRESS, RTP_PORT
)
from src.signaling.messages import (
    Invite, Accept, Reject, Bye, ProtocolError, encode, decode
)

class CallRejected(Exception):
    pass

class CallFailed(Exception):
    pass

async def invite(host, port, rtp_port):
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), TIME_OUT)
    except asyncio.TimeoutError as e:
        raise CallFailed(f"Timed out connecting to {host}:{port}") from e
    except OSError as e:
        raise CallFailed(f"Cannot connect to {host}:{port}: {e}") from e
    try:
        call_id = uuid.uuid4().hex

        try:
            writer.write(encode(Invite(call_id=call_id, rtp_port=rtp_port)))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), TIME_OUT)
        except asyncio.TimeoutError:
            raise CallFailed(f"No reply from {host}:{port}") from e
        except ConnectionError as e:
            raise CallFailed(f"Connection error with {host}:{port}: {e}") from e
        
        if not line:
            raise CallFailed(f"Callee closed connection before replying")

        try:
            msg = decode(line)
        except ProtocolError as e:
            raise CallFailed(f"Malformed message: {e}") from e

        if isinstance(msg, Reject):
            raise CallRejected(msg.reason)

        if not isinstance(msg, Accept):
            raise CallFailed(f"Expected ACCEPT, got {msg.TYPE}")

        if call_id != msg.call_id:
            raise CallFailed(f"Expected call {call_id}, got {msg.call_id}")

        call_info = {
            "call_id": call_id,
            "peer_ip": writer.get_extra_info("peername")[0],
            "peer_rtp_port": msg.rtp_port,
            "local_rtp_port": rtp_port
        }

        return call_info, reader, writer
    
    except:
        try:
            writer.close()
            await writer.wait_closed()
        except ConnectionError:
            pass
        raise

async def hangup(call_info: dict, writer: asyncio.StreamWriter):
    try:
        writer.write(encode(Bye(call_info["call_id"])))
        await writer.drain()
    except ConnectionError:
        pass
    try:
        writer.close()
        await writer.wait_closed()
    except ConnectionError:
        pass

async def _demo():
    try:
        call_info, reader, writer = await invite(CALLEE_ADDRESS, TCP_PORT, RTP_PORT)
    except CallRejected as e:
        print(f"Call rejected: {e}")
        return
    except CallFailed as e:
        print(f"Call failed: {e}")
        return

    print(f"Call established: {call_info}")
    await asyncio.sleep(3)              # stand-in for media
    await hangup(call_info, writer)
    print("Call ended")

if __name__ == "__main__":
    asyncio.run(_demo())