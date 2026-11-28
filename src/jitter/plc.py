# packet loss concealment: repeat the last good frame, quieter each time the loss goes on
import numpy as np

from src.config import SAMPLES_PER_FRAME


class LossConcealer:
    """
    good(pcm)    call for every frame that was decoded from a real packet
    conceal()    call when the jitter buffer hands back a slot with no payload

    The n-th lost frame in a row is the last good frame times attenuation ** (n - 1),
    and after mute_after lost frames in a row the output is silence.
    """

    def __init__(self, attenuation: float = 0.7, mute_after: int = 8,
                 frame_samples: int = SAMPLES_PER_FRAME):
        if not 0 < attenuation <= 1:
            raise ValueError(f"attenuation must be in (0, 1], got {attenuation}")
        if mute_after < 1:
            raise ValueError(f"mute_after must be at least 1, got {mute_after}")
        self.attenuation = attenuation
        self.mute_after = mute_after
        self.frame_samples = frame_samples
        self._last = None
        self._run = 0                   # lost frames in a row
        self.concealed_total = 0

    @property
    def consecutive_losses(self) -> int:
        return self._run

    def good(self, pcm: np.ndarray) -> np.ndarray:
        self._last = np.asarray(pcm, dtype=np.int16).copy()
        self._run = 0
        return pcm

    def conceal(self) -> np.ndarray:
        self._run += 1
        self.concealed_total += 1
        if self._last is None or self._run > self.mute_after:
            return np.zeros(self.frame_samples, dtype=np.int16)
        gain = self.attenuation ** (self._run - 1)
        return np.round(self._last.astype(np.float32) * gain).astype(np.int16)


if __name__ == "__main__":
    plc = LossConcealer()
    plc.good(np.full(SAMPLES_PER_FRAME, 10000, dtype=np.int16))
    print([int(plc.conceal()[0]) for _ in range(10)])     # 10000 7000 4900 ... 0 0