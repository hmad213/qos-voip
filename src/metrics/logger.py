# per packet CSV. One row per packet the receiver saw, plus rows for packets that never arrived.
# Close it (or use "with") when BYE arrives so the last rows are flushed.
import csv
import os

from src.metrics.stats import PacketRecord

FIELDS = ["ext_seq", "seq", "rtp_timestamp", "send_time", "arrival_time",
          "owd_ms", "jitter_ms", "status", "reordered"]
# status: ok, late, duplicate, lost


def _fmt(value, digits):
    return "" if value is None else f"{value:.{digits}f}"


class PacketLogger:
    def __init__(self, path: str, flush_every: int = 100):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.path = path
        self.flush_every = flush_every
        self._file = open(path, "w", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=FIELDS)
        self._writer.writeheader()
        self._rows = 0

    def _write(self, row: dict) -> None:
        if self._file is None:
            raise ValueError("logger is closed")
        self._writer.writerow(row)
        self._rows += 1
        if self._rows % self.flush_every == 0:
            self._file.flush()

    def log(self, record: PacketRecord) -> None:
        self._write({
            "ext_seq": record.ext_seq,
            "seq": record.seq,
            "rtp_timestamp": record.rtp_timestamp,
            "send_time": _fmt(record.send_time, 6),
            "arrival_time": _fmt(record.arrival_time, 6),
            "owd_ms": _fmt(record.owd_ms, 3),
            "jitter_ms": _fmt(record.jitter_ms, 3),
            "status": record.status,
            "reordered": int(record.reordered),
        })

    def log_lost(self, ext_seq: int) -> None:
        self._write({"ext_seq": ext_seq, "seq": ext_seq & 0xFFFF, "status": "lost"})

    def log_missing(self, stats) -> None:
        # call once at the end of the call: adds a "lost" row for every gap
        for ext_seq in stats.lost_sequences():
            self.log_lost(ext_seq)

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()