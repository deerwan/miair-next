"""音频时长嗅探 - 元数据缺失 duration 时的兜底探测

部分 DLNA 控制端 (如 Feishin) 推送的 DIDL-Lite <res> 不携带 duration 属性,
导致渲染器曲长恒为 0, 引发 Seek 降级软 Seek、无法自动切歌、时间跑飞等问题。

探测策略 (分层):
1. PyAV (av>=10, 项目已有依赖): 从内存数据读取容器时长, 覆盖 ffmpeg 全格式;
2. 内置纯解析回退: WAV (RIFF/data + byte_rate) 与 FLAC (STREAMINFO) 头解析,
   PyAV 不可用或探测失败时使用。

探测仅需容器头部数据 (FLAC STREAMINFO / WAV fmt+data / MP3 前若干帧),
无需等待完整下载; 截取前 _PROBE_MAX_BYTES 以限制拷贝开销。
"""

from __future__ import annotations

import asyncio
import io
import struct

# 探测所需数据上限: 容器头部远小于此值, 截取以避免大文件全量拷贝
_PROBE_MAX_BYTES = 2 * 1024 * 1024


def probe_duration_sync(data: bytes | bytearray) -> float:
    """从音频数据嗅探时长 (秒); 无法确定时返回 0.0"""
    if not data:
        return 0.0
    if len(data) > _PROBE_MAX_BYTES:
        # memoryview 切片零拷贝, bytes() 仅一次拷贝
        data = bytes(memoryview(data)[:_PROBE_MAX_BYTES])
    duration = _probe_with_av(data)
    if duration <= 0:
        duration = _probe_wav_flac_header(data)
    return duration if duration > 0 else 0.0


async def probe_duration(data: bytes | bytearray) -> float:
    """probe_duration_sync 的异步包装 (av.open 头解析放线程池, 不阻塞事件循环)"""
    return await asyncio.to_thread(probe_duration_sync, data)


def _probe_with_av(data: bytes | bytearray) -> float:
    """使用 PyAV 从内存读取容器时长"""
    try:
        import av
    except ImportError:
        return 0.0
    try:
        with av.open(io.BytesIO(bytes(data))) as container:
            duration = container.duration
            if duration and duration > 0:
                return duration / 1_000_000.0
            for stream in container.streams:
                if stream.duration and stream.duration > 0 and stream.time_base:
                    return float(stream.duration) * float(stream.time_base)
    except Exception:
        return 0.0
    return 0.0


def _probe_wav_flac_header(data: bytes | bytearray) -> float:
    """纯解析回退: WAV / FLAC 容器头"""
    try:
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return _probe_wav_header(data)
        if len(data) >= 42 and data[:4] == b"fLaC":
            return _probe_flac_header(data)
    except Exception:
        pass
    return 0.0


def _probe_wav_header(data: bytes | bytearray) -> float:
    """WAV: 遍历 RIFF chunk, duration = data 大小 / byte_rate"""
    pos = 12
    byte_rate = 0
    data_size = 0
    while pos + 8 <= len(data):
        chunk_id = bytes(data[pos : pos + 4])
        chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
        if chunk_id == b"fmt " and pos + 8 + 16 <= len(data):
            byte_rate = struct.unpack_from("<I", data, pos + 16)[0]
        elif chunk_id == b"data":
            data_size = chunk_size
            if byte_rate:
                break
        if chunk_size <= 0:
            break
        pos += 8 + chunk_size + (chunk_size & 1)
    if byte_rate > 0 and data_size > 0:
        return data_size / byte_rate
    return 0.0


def _probe_flac_header(data: bytes | bytearray) -> float:
    """FLAC: 解析 STREAMINFO (首元数据块) 的 sample_rate 与 total_samples

    STREAMINFO 数据起始于文件偏移 8 (4 fLaC + 4 块头), 字段为大端位流:
    sample_rate 20 bits @ byte10-12 高位;
    total_samples 36 bits @ byte13 低 4 位 + byte14-17
    """
    si = 8
    sample_rate = (data[si + 10] << 12) | (data[si + 11] << 4) | (data[si + 12] >> 4)
    total_samples = (
        ((data[si + 13] & 0x0F) << 32)
        | (data[si + 14] << 24)
        | (data[si + 15] << 16)
        | (data[si + 16] << 8)
        | data[si + 17]
    )
    if sample_rate > 0 and total_samples > 0:
        return total_samples / sample_rate
    return 0.0