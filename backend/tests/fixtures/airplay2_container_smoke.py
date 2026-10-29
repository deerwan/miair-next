"""Run through the production image entrypoint, without a Xiaomi account.

Checks standard backend startup and real Shairport/NQPTP metadata lifecycle.
No physical audio, speaker, or cloud-catalog result is implied.
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.engine.airplay.shairport_server import ShairportSyncServer

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWLsAAAAASUVORK5CYII="
)


def verify_web_backend():
    process = subprocess.Popen(
        [sys.executable, "run.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={**os.environ, "MIAIR_WEB_HOST": "127.0.0.1", "MIAIR_HOSTNAME": "127.0.0.1"},
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("Standard backend exited before becoming healthy")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8300/api/v1/health", timeout=1) as response:
                    health = json.load(response)
                assert health["status"] == "ok", health
                uid = next(line for line in Path(f"/proc/{process.pid}/status").read_text().splitlines()
                           if line.startswith("Uid:"))
                assert int(uid.split()[1]) == os.getuid() != 0, uid
                print("Standard backend healthy:", health, uid, flush=True)
                return
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        raise AssertionError("Standard backend did not become healthy")
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
        if process.returncode not in (0, -15):
            print(output.decode(errors="replace")[-4000:], file=sys.stderr)


def track(title):
    body = b""
    for code, value in (("minm", title), ("asar", "Container smoke artist"), ("asal", "Smoke album")):
        encoded = value.encode()
        body += code.encode() + struct.pack("!I", len(encoded)) + encoded
    return b"mlit" + struct.pack("!I", len(body)) + body


async def send_metadata(reader, writer, sequence, content_type, body, token):
    writer.write((
        f"SET_PARAMETER rtsp://127.0.0.1/smoke RTSP/1.0\r\nCSeq: {sequence}\r\n"
        f"Content-Type: {content_type}\r\nRTP-Info: rtptime={token}\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body)
    await writer.drain()
    response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    assert response.startswith(b"RTSP/1.0 200"), response


async def eventually(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Native metadata did not reach the adapter")


async def verify_native_receiver():
    server = ShairportSyncServer("127.0.0.1", "MiAir standard image smoke")
    assert server._timed_stdout
    for cycle in range(2):
        writer = None
        directory = ""
        children = []
        try:
            await server.start()
            assert server.running, server.last_error
            assert server._metadata_dir is not None
            directory = server._metadata_dir.name
            children = [server._shairport_process, server._nqptp_process]
            for process in children:
                uid = next(line for line in Path(f"/proc/{process.pid}/status").read_text().splitlines()
                           if line.startswith("Uid:"))
                assert int(uid.split()[1]) == os.getuid() != 0, uid
            reader, writer = await asyncio.open_connection("127.0.0.1", server.rtsp_port)
            await send_metadata(reader, writer, 1, "application/x-dmap-tagged", track("First"), 100)
            await eventually(lambda: server.daap_meta and server.daap_meta["title"] == "First")
            await send_metadata(reader, writer, 2, "image/png", PNG, 100)
            await eventually(lambda: server.artwork is not None)
            assert server.artwork[1:] == ("image/png", PNG)
            await send_metadata(reader, writer, 3, "application/x-dmap-tagged", track("Next"), 200)
            await eventually(lambda: server.daap_meta and server.daap_meta["title"] == "Next")
            assert server.artwork is None
            print(f"Cycle {cycle + 1}: native receiver and RTSP -> FIFO metadata passed", flush=True)
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()
            await server.stop()
        assert children and all(process.poll() is not None for process in children)
        assert directory and not Path(directory).exists()
        assert not Path("/dev/shm/nqptp").exists()
        assert server.artwork is None
        assert server._metadata_thread is None
        print(f"Cycle {cycle + 1}: native children, private FIFO and PTP clock cleaned", flush=True)


def main():
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("miair").setLevel(logging.WARNING)
    assert os.getuid() != 0, "The production entrypoint must drop backend privileges"
    assert Path("/proc/sys/net/ipv4/ip_unprivileged_port_start").read_text().strip() == "1024"
    for binary in ("shairport-sync", "nqptp", "avahi-daemon", "dbus-daemon"):
        assert shutil.which(binary), f"Standard image is missing {binary}"
    version = subprocess.check_output(["shairport-sync", "-V"], text=True)
    assert "AirPlay2" in version and "metadata" in version, version
    capability = subprocess.check_output(["getcap", "/usr/local/bin/nqptp"], text=True)
    assert "cap_net_bind_service=ep" in capability, capability
    print("Standard image capabilities:", version.strip(), capability.strip(), flush=True)
    verify_web_backend()
    asyncio.run(verify_native_receiver())
    print("Standard installation smoke passed", flush=True)


if __name__ == "__main__":
    main()
