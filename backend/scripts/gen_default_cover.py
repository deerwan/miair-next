"""生成通用默认封面 default_cover.png（仅用标准库，无第三方依赖）。

输出: app/engine/dlna/assets/default_cover.png
设计: 深蓝径向渐变背景 + 居中半透明白色圆环 + 音符造型，风格中性通用。
"""
import os
import struct
import zlib

W = H = 512
OUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "app", "engine", "dlna", "assets", "default_cover.png",
)

cx = cy = W // 2


def lerp(a, b, t):
    return int(a + (b - a) * t)


def px(x, y):
    """返回 (r, g, b)"""
    # 径向渐变：中心亮一点的蓝，边缘深蓝
    dx = (x - cx) / (W / 2)
    dy = (y - cy) / (H / 2)
    d = min(1.0, (dx * dx + dy * dy) ** 0.5)
    r = lerp(40, 14, d)
    g = lerp(78, 28, d)
    b = lerp(140, 70, d)

    # 居中圆环 (半径 ~150)
    dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    ring_r = 150.0
    if abs(dist - ring_r) < 6:
        rr = lerp(r, 220, 0.85)
        gg = lerp(g, 230, 0.85)
        bb = lerp(b, 245, 0.85)
        return rr, gg, bb

    # 圆环内的音符 (简单的两个圆 + 一根竖线 + 旗)
    # 左音符头
    bx1, by1, br1 = cx - 70, cy + 30, 26
    # 右音符头
    bx2, by2, br2 = cx + 10, cy + 50, 24
    for (bx, by, brd) in ((bx1, by1, br1), (bx2, by2, br2)):
        if (x - bx) ** 2 + (y - by) ** 2 <= brd * brd:
            return 235, 240, 250
    # 符干 (竖线)
    stem_x = cx + 34
    if bx2 - br2 < x <= stem_x and cy - 70 < y <= by2 + br2:
        return 235, 240, 250
    # 旗 (右上斜线)
    if cy - 70 < y < cy - 10 and stem_x < x <= stem_x + 36 and (x - stem_x) < (y - (cy - 70)) * 0.6 + 14:
        return 235, 240, 250

    return r, g, b


def make_png():
    raw = bytearray()
    for y in range(H):
        raw.append(0)  # filter type 0
        for x in range(W):
            r, g, b = px(x, y)
            raw.extend((r, g, b))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(bytes(raw), 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    return png


if __name__ == "__main__":
    data = make_png()
    with open(OUT, "wb") as f:
        f.write(data)
    print(f"wrote {len(data)} bytes -> {os.path.normpath(OUT)}")
