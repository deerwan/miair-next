"""音频格式检测 / seek 逻辑 / 日志编码测试 (移植自 MiAir tests/test_audio_seek.py)

导入路径由 miair.dlna.device_server 调整为 app.engine.dlna.device_server。
以 pytest 风格编写, 在 backend/ 目录下执行 `pytest` 即可收集。
"""

import asyncio
import os
import struct

from app.engine.dlna.device_server import DeviceServer


def test_detect_audio_format():
    """_detect_audio_format 魔数检测"""
    # FLAC
    assert DeviceServer._detect_audio_format(b"fLaC" + b"\x00" * 100) == "flac"
    # WAV
    assert DeviceServer._detect_audio_format(b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 100) == "wav"
    # OGG
    assert DeviceServer._detect_audio_format(b"OggS" + b"\x00" * 100) == "ogg"
    # MP3 with ID3
    assert DeviceServer._detect_audio_format(b"ID3" + b"\x00" * 100) == "mp3"
    # MP3 sync frame
    assert DeviceServer._detect_audio_format(bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 100) == "mp3"
    # AAC ADTS
    assert DeviceServer._detect_audio_format(bytes([0xFF, 0xF1, 0x50, 0x80]) + b"\x00" * 100) == "aac"
    # M4A (ftyp)
    assert DeviceServer._detect_audio_format(b"\x00\x00\x00\x20" + b"ftyp" + b"M4A " + b"\x00" * 100) == "m4a"
    # WMA
    assert DeviceServer._detect_audio_format(b"\x30\x26\xb2\x75" + b"\x00" * 100) == "wma"
    # Unknown
    assert DeviceServer._detect_audio_format(b"\x00\x01\x02\x03" + b"\x00" * 100) == "unknown"


def test_format_seek_uses_magic():
    """_format_seek 使用魔数而非 content_type"""
    streaminfo = bytearray(34)
    streaminfo[0:2] = (4096).to_bytes(2, "big")
    streaminfo[2:4] = (4096).to_bytes(2, "big")
    sr, ch_minus1, bps_minus1 = 44100, 1, 15
    val = (sr << 12) | (ch_minus1 << 9) | (bps_minus1 << 4) | 0
    streaminfo[10:14] = val.to_bytes(4, "big")
    streaminfo[14:18] = (1000000).to_bytes(4, "big")
    block_header = bytes([0x80, 0x00, 0x00, 0x22])
    flac_header = b"fLaC" + block_header + bytes(streaminfo)

    audio_data = bytearray(100000)
    for offset in [0, 20000, 40000, 60000, 80000]:
        audio_data[offset] = 0xFF
        audio_data[offset + 1] = 0xF8
    flac_data = bytearray(flac_header + bytes(audio_data))

    # content_type 是 application/octet-stream, 但数据是 FLAC
    result = DeviceServer._format_seek(flac_data, 0.5, "application/octet-stream")
    assert result is not None
    assert result[:4] == b"fLaC"

    # MP3
    mp3_data = bytearray(50000)
    mp3_data[0] = 0xFF
    mp3_data[1] = 0xFB
    for i in range(2000, 50000, 2000):
        mp3_data[i] = 0xFF
        mp3_data[i + 1] = 0xFB
    result = DeviceServer._format_seek(mp3_data, 0.5, "application/octet-stream")
    assert result is not None
    assert result[0] == 0xFF and (result[1] & 0xE0) == 0xE0


def test_seek_wav():
    """WAV seek"""
    sample_rate, channels, bits_per_sample = 44100, 2, 16
    block_align = channels * (bits_per_sample // 8)
    byte_rate = sample_rate * block_align

    fmt_data = struct.pack("<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt_data)) + fmt_data

    num_samples = 10000
    audio_bytes = bytearray(num_samples * block_align)
    for i in range(0, len(audio_bytes), 2):
        audio_bytes[i] = i & 0xFF
        audio_bytes[i + 1] = (i >> 8) & 0xFF
    data_chunk = b"data" + struct.pack("<I", len(audio_bytes)) + bytes(audio_bytes)

    riff_size = 4 + len(fmt_chunk) + len(data_chunk)
    wav_data = bytearray(b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + fmt_chunk + data_chunk)

    result = DeviceServer._seek_wav(wav_data, 0.5)
    assert result is not None
    assert result[:4] == b"RIFF"
    assert result[8:12] == b"WAVE"
    assert len(result) < len(wav_data)


def test_seek_aac():
    """AAC ADTS seek"""
    aac_data = bytearray(20000)
    for offset in [0, 5000, 10000, 15000]:
        aac_data[offset] = 0xFF
        aac_data[offset + 1] = 0xF1
    result = DeviceServer._seek_aac(aac_data, 0.5)
    assert result is not None
    assert result[0] == 0xFF and (result[1] & 0xF6) == 0xF0


def test_mp3_id3_skip():
    """MP3 seek 跳过 ID3v2 头"""
    id3_body_size = 1000
    id3_header = b"ID3\x04\x00\x00"
    s = id3_body_size
    id3_header += bytes([(s >> 21) & 0x7F, (s >> 14) & 0x7F, (s >> 7) & 0x7F, s & 0x7F])
    id3_data = id3_header + bytearray(id3_body_size)

    audio_data = bytearray(50000)
    for i in range(0, 50000, 2000):
        audio_data[i] = 0xFF
        audio_data[i + 1] = 0xFB

    mp3_data = bytearray(id3_data + audio_data)
    result = DeviceServer._seek_mp3(mp3_data, 0.5)
    assert result is not None
    assert result[0] == 0xFF and (result[1] & 0xE0) == 0xE0
    assert result[:3] != b"ID3"


def test_check_ffmpeg():
    """_check_ffmpeg 不抛异常 (找到与否都可接受)"""
    ds = DeviceServer("127.0.0.1", 8200)
    result = ds._check_ffmpeg()
    assert result is None or isinstance(result, str)


def test_logging_encoding():
    """日志编码不会因特殊字符崩溃"""
    import io
    import logging

    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace", line_buffering=True)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_encoding")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    for text in ["願い〜あの頃のキミへ〜", "日本語テスト", "🎵🎶 emoji test", "Ñoño café résumé", "中文测试：标题《歌曲》"]:
        logger.info(text)  # 不应抛 UnicodeEncodeError

    logger.removeHandler(handler)


def test_ffmpeg_seek_flac():
    """用 ffmpeg 对 FLAC 数据 seek (ffmpeg 不可用时跳过断言)"""
    import subprocess
    import tempfile

    ds = DeviceServer("127.0.0.1", 8200)
    ffmpeg = ds._check_ffmpeg()
    if not ffmpeg:
        return  # ffmpeg 不可用, 跳过 (不影响纯 Python seek)

    out_fd, out_path = tempfile.mkstemp(suffix=".flac", prefix="miair_test_")
    os.close(out_fd)
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:a", "flac", out_path],
            capture_output=True, timeout=10,
        )
        if proc.returncode != 0:
            return
        with open(out_path, "rb") as f:
            flac_data = bytearray(f.read())

        assert DeviceServer._detect_audio_format(flac_data) == "flac"

        seeked = asyncio.run(ds._ffmpeg_seek(flac_data, 2.5, "application/octet-stream"))
        if seeked:
            assert len(seeked) > 0
            assert DeviceServer._detect_audio_format(seeked) == "flac"
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
