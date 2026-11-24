import numpy as np

BIAS = 0x84          # 132
CLIP = 32635

# exponent table: for i in 1..255, floor(log2(i)); used to find the segment
_EXP_TABLE = np.zeros(256, dtype=np.int32)
for i in range(1, 256):
    _EXP_TABLE[i] = i.bit_length() - 1


_DECODE_TABLE = np.zeros(256, dtype=np.int16)
for byte in range(256):
    u = ~byte & 0xFF                 # undo the final bit inversion
    sign = u & 0x80
    segment = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa << 3) + BIAS) << segment) - BIAS
    _DECODE_TABLE[byte] = -sample if sign else sample


def encode(pcm: np.ndarray) -> bytes:
    # int16 samples to one µ-law byte per sample
    x = np.asarray(pcm).astype(np.int32)      # widening to avoid overflow

    sign = (x < 0).astype(np.int32)
    mag = np.minimum(np.abs(x), CLIP) + BIAS  # clip, then add bias

    segment = _EXP_TABLE[mag >> 7]            
    mantissa = (mag >> (segment + 3)) & 0x0F  # 4 bits below the top set bit

    ulaw = ~((sign << 7) | (segment << 4) | mantissa) & 0xFF
    return ulaw.astype(np.uint8).tobytes()


def decode(data: bytes) -> np.ndarray:
    # µ-law bytes to int16 samples
    return _DECODE_TABLE[np.frombuffer(data, dtype=np.uint8)]


if __name__ == "__main__":
    # known values
    print(encode(np.array([0], dtype=np.int16)).hex())            # expect: ff
    print(decode(b"\x00")[0], decode(b"\x80")[0], decode(b"\xff")[0])  # -32124 32124 0

    # frame length: 160 samples -> 160 bytes -> 160 samples
    frame = np.zeros(160, dtype=np.int16)
    print(len(encode(frame)), len(decode(encode(frame))))         # 160 160

    # round trip on a sine wave
    t = np.arange(8000) / 8000
    sine = (10000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    back = decode(encode(sine))
    err = np.abs(sine.astype(np.int32) - back.astype(np.int32))
    print("max error:", err.max())

    # all 256 bytes survive decode -> encode (0x7F becomes 0xFF, "negative zero")
    all_bytes = bytes(range(256))
    again = encode(decode(all_bytes))
    print([hex(b) for b, a in zip(all_bytes, again) if b != a])   # expect: ['0x7f']