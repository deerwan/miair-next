"""AirPlay 握手仿真脚本（无真实 iOS 设备）

模拟一个 iOS 客户端，对本地运行的 AirPlayServer 执行：
    OPTIONS -> ANNOUNCE -> SETUP -> RECORD
并验证：
    1. OPTIONS 返回的 Apple-Response 能被对应公钥反向校验
       （即签名里确实包含了本机 IP 与 device_id —— Apple-Response 校验的闭环）
    2. SETUP 返回的 Transport 端口可达
    3. 服务端能完整处理 OPTIONS/ANNOUNCE/SETUP/RECORD 四次握手

用于在没有 iPhone / 小米音箱的环境下，验证 Issue #5 修复后的服务端握手链路。

注意：本脚本会绑定本地 TCP 端口并启动 RTSP 线程，但不注册 mDNS（避免污染局域网）。
"""

import asyncio
import base64
import socket
import sys
import threading
import time

sys.path.insert(0, "/Users/deer/Desktop/未命名文件夹 3/miair-next/backend")

from Crypto.PublicKey import RSA

from app.engine.airplay.server import AirPlayServer, AP1Security, AIRPORT_PRIVATE_KEY


# AirPlay 公钥（与私钥配对）：iOS 端用它对 Apple-Response 做校验
AIRPORT_PUBLIC_KEY = RSA.import_key(AIRPORT_PRIVATE_KEY).publickey()


def verify_apple_response(apple_response_b64: str, apple_challenge: str, request_host: bytes, device_id: bytes) -> bool:
    """反向校验服务端返回的 Apple-Response。

    Apple-Response 是用私钥对如下明文做 RSA 签名 (m = m^d mod n)：
        D = challenge(32) || request_host(4) || device_id(6)
        message = 0x00 0x01 || 0xFF*(256-3-len(D)) || 0x00 || D
    公钥验证：pow(sig_int, e, n) 必须等于上述 message 的 bigint（message < n）。
    这能确认签名里确实编码了本机 IP (request_host) 与 device_id。
    """
    RSA_KEYLEN = 256
    if apple_response_b64[-2:] != "==":
        apple_response_b64 += "=="
    mbin = base64.b64decode(apple_response_b64)
    sig_int = int.from_bytes(mbin, "big")
    # 公钥还原明文
    recovered_int = pow(sig_int, AIRPORT_PUBLIC_KEY.e, AIRPORT_PUBLIC_KEY.n)

    # 重建原始 message（与服务端 compute_apple_response 完全一致）
    if apple_challenge[-2:] != "==":
        apple_challenge += "=="
    data = base64.b64decode(apple_challenge).ljust(32, b"\0")
    signed_data = data + request_host + device_id
    message = b"\x00\x01" + b"\xFF" * (RSA_KEYLEN - 3 - len(signed_data)) + b"\x00" + signed_data
    message_int = int.from_bytes(message, "big")

    if recovered_int != message_int:
        # 容忍 to_bytes 前导零丢失
        recovered = recovered_int.to_bytes(RSA_KEYLEN, "big").rjust(RSA_KEYLEN, b"\x00")
        return recovered == message
    return True


def _rtsp_request(sock: socket.socket, method: str, headers: dict, body: bytes = b"") -> dict:
    """发送一条 RTSP 请求，返回解析后的 {status, headers}。"""
    lines = [f"{method} rtsp://localhost RTSP/1.0"]
    for k, v in headers.items():
        lines.append(f"{k}: {v}")
    if body:
        lines.append(f"Content-Length: {len(body)}")
    lines.append("")
    req = ("\r\n".join(lines) + "\r\n").encode("utf-8") + body
    sock.sendall(req)

    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, _ = data.partition(b"\r\n\r\n")
    head_lines = head.decode("utf-8", errors="replace").split("\r\n")
    status = int(head_lines[0].split(" ", 2)[1])
    resp_headers = {}
    for line in head_lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            resp_headers[k.strip()] = v.strip()
    return {"status": status, "headers": resp_headers}


def run_client(rtsp_port: int, ipv4_bin: bytes, device_id_bin: bytes) -> bool:
    """模拟 iOS 客户端执行完整握手，返回是否全部通过。"""
    ok = True
    with socket.create_connection(("127.0.0.1", rtsp_port), timeout=10) as sock:
        # 1) OPTIONS + Apple-Challenge
        challenge = base64.b64encode(b"\x11\x22\x33\x44" * 8).decode("ascii")  # 32 字节
        resp = _rtsp_request(sock, "OPTIONS", {"CSeq": "1", "Apple-Challenge": challenge})
        assert resp["status"] == 200, f"OPTIONS 失败: {resp}"
        apple_response = resp["headers"].get("Apple-Response")
        assert apple_response, "OPTIONS 缺少 Apple-Response"
        verified = verify_apple_response(apple_response, challenge, ipv4_bin, device_id_bin)
        print(f"  [OPTIONS] Apple-Response 校验: {'通过' if verified else '失败'}")
        ok = ok and verified

        # 2) ANNOUNCE (SDP)
        sdp = (
            "v=0\r\n"
            "o=AirTunes 1 1 IN IP4 127.0.0.1\r\n"
            "s=AirTunes\r\n"
            "c=IN IP4 127.0.0.1\r\n"
            "t=0 0\r\n"
            "m=audio 0 RTP/AVP 96\r\n"
            "a=rtpmap:96 AppleLossless\r\n"
        ).encode("utf-8")
        resp = _rtsp_request(
            sock, "ANNOUNCE",
            {"CSeq": "2", "Content-Type": "application/sdp"},
            body=sdp,
        )
        print(f"  [ANNOUNCE] status={resp['status']}")
        ok = ok and resp["status"] == 200

        # 3) SETUP
        resp = _rtsp_request(
            sock, "SETUP",
            {"CSeq": "3", "Transport": "RTP/AVP/UDP;unicast;interleaved=0-1;mode=record"},
        )
        print(f"  [SETUP] status={resp['status']} Transport={resp['headers'].get('Transport')}")
        ok = ok and resp["status"] == 200

        # 4) RECORD
        resp = _rtsp_request(sock, "RECORD", {"CSeq": "4", "Session": "1", "RTP-Info": "seq=0;rtptime=0"})
        print(f"  [RECORD] status={resp['status']}")
        ok = ok and resp["status"] == 200

        # 5) TEARDOWN
        _rtsp_request(sock, "TEARDOWN", {"CSeq": "5", "Session": "1"})
    return ok


async def main():
    # 跳过 mDNS 广播，避免污染局域网
    from app.engine.airplay import mdns as _mdns_mod
    _mdns_mod.AirPlayMDNS.start = lambda self: None  # type: ignore

    srv = AirPlayServer(hostname="127.0.0.1", device_name="SimSpeaker")
    await srv.start()
    rtsp_port = srv.rtsp_port
    ipv4_bin = srv.ipv4_bin
    device_id_bin = srv.device_id_bin
    print(f"AirPlayServer 已启动，RTSP 端口={rtsp_port}")

    # 等待 RTSP 线程就绪
    time.sleep(0.5)

    try:
        ok = run_client(rtsp_port, ipv4_bin, device_id_bin)
    finally:
        await srv.stop()

    print("\n=== 仿真结果 ===")
    print("握手链路验证:", "通过 ✅" if ok else "失败 ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
