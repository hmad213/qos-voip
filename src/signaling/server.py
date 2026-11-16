# Callee code. Listens, validates INVITE, opens the UDP receiver, and sends ACCEPT
import asyncio
from src.signaling.messages import (
    encode, decode, Reject, Accept, Bye, Invite, ProtocolError
)
from src.config import CODEC_NAME, PACKET_TIME, TCP_PORT, RTP_PORT, TIME_OUT

current_call = None

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter)->None:
    global current_call
    addr = writer.get_extra_info("peername")
    try:
        try:
            line = await asyncio.wait_for(reader.readline(), TIME_OUT)
        except asyncio.TimeoutError:
            print(f"No INVITE from {addr}, closing...")
            return
        if not line:
            return

        try:
            message = decode(line)
        except ProtocolError as e:
            writer.write(encode(Reject(".", f"malformed message: {e}")))
            await writer.drain()
            return

        if not isinstance(message, Invite):
            writer.write(encode(Reject(message.call_id, f"Message type is not Invite! got {message.TYPE}")))
            await writer.drain()
            return
        if current_call is not None:
            writer.write(encode(Reject(message.call_id), "Server busy"))
            await writer.drain()
            return
        if message.codec != CODEC_NAME:
            writer.write(encode(Reject(message.call_id), f"Codec not supported: {message.codec}"))
            await writer.drain()
            return
        if message.packet_time != PACKET_TIME:
            writer.write(encode(Reject(message.call_id), f"Packet time not supported: {message.packet_time}"))
            await writer.drain()
            return

        current_call = message.call_id
        try:
            writer.write(encode(Accept(message.call_id, RTP_PORT)))
            print(f"Call {message.call_id} accepted!")

            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    next_msg = decode(line)
                except ProtocolError:
                    continue
                if isinstance(next_msg, Bye) and next_msg.call_id == message.call_id:
                    break

            print(f"Call {message.call_id} ended!")
        finally:
            current_call = None
    except ConnectionError as e:
        print(f"Connection error with {addr}: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except ConnectionError:
            pass
            

        
async def run()->None:
    server = await asyncio.start_server(handle_client, "0.0.0.0", TCP_PORT)
    async with server:
        await server.serve_forever()