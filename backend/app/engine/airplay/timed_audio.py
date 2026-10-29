"""MiAir timed-stdout v1. PTP deadlines are in Linux CLOCK_MONOTONIC_RAW.

The clock is shared with Shairport in the container, not time.time(), and not
Python's CLOCK_MONOTONIC (which may be frequency-adjusted by NTP).
"""

import struct
import time
from dataclasses import dataclass

HEADER = struct.Struct("<4sIIIQQ")
MAGIC = b"MAP2"
MAX_FRAMES = 4096
FRAME_BYTES = 4
RATE = 48000


def clock_ns() -> int:
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)


def read_exact(pipe, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        part = pipe.read(size - len(result))
        if not part:
            if result:
                raise ValueError("truncated timed-stdout record")
            return b""
        result.extend(part)
    return bytes(result)


def read_record(pipe):
    header = read_exact(pipe, HEADER.size)
    if not header:
        return None
    magic, frames, rtp, kind, playtime, epoch = HEADER.unpack(header)
    if magic != MAGIC or frames > MAX_FRAMES or kind not in (0, 1, 2):
        raise ValueError("invalid timed-stdout v1 header")
    if kind == 2 and frames:
        raise ValueError("invalid timed-stdout boundary")
    data = read_exact(pipe, frames * FRAME_BYTES) if frames else b""
    if len(data) != frames * FRAME_BYTES:
        raise ValueError("missing timed-stdout PCM")
    return kind, rtp, playtime, epoch, data


@dataclass(frozen=True)
class TimedPCM:
    data: bytes
    playtime_ns: int

    def trim_before(self, earliest_ns: int):
        """Discard expired *whole stereo frames*, retaining the PTP deadline."""
        late_ns = earliest_ns - self.playtime_ns
        if late_ns <= 0:
            return self, 0
        frames = min(len(self.data) // FRAME_BYTES,
                     (late_ns * RATE + 999_999_999) // 1_000_000_000)
        if frames * FRAME_BYTES == len(self.data):
            return None, frames
        return TimedPCM(self.data[frames * FRAME_BYTES:],
                        self.playtime_ns + frames * 1_000_000_000 // RATE), frames
