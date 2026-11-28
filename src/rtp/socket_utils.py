# creates the UDP socket for RTP and sets the IP_TOS byte (DSCP << 2) on it
import socket

from src.config import TOS_BYTE


def make_udp_socket(local_ip: str = "0.0.0.0", local_port: int = 0, tos: int = TOS_BYTE,
                    rcvbuf: int = 0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, tos)
        if rcvbuf:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        sock.bind((local_ip, local_port))
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


def get_tos(sock: socket.socket) -> int:
    return sock.getsockopt(socket.IPPROTO_IP, socket.IP_TOS)


if __name__ == "__main__":
    s = make_udp_socket("127.0.0.1", 0)
    print(get_tos(s), TOS_BYTE)                 # 184 184
    s.close()