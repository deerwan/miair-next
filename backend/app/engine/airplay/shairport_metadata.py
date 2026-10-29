"""Bounded reader for Shairport Sync's XML/base64 metadata pipe.

The pipe is separate from PCM. Snapshots cross the native reader/API threads;
no artwork files or sender-supplied URLs are exposed to the web server.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import threading
import xml.etree.ElementTree as ET
from collections import deque

from app.engine.airplay.server import (
    _derive_title_from_artist,
    _strip_artist_prefix,
    _strip_artist_suffix,
)

MAX_ARTWORK_BYTES = 4 * 1024 * 1024
MAX_ITEM_BYTES = (MAX_ARTWORK_BYTES + 2) // 3 * 4 + 1024
_FIELDS = {"minm": "title", "asar": "artist", "asal": "album"}


class ShairportMetadata:
    def __init__(self):
        self._lock = threading.RLock()
        self._buffer = bytearray()
        self._sequence = 0
        self.reset()

    def reset(self):
        with self._lock:
            self._meta = None
            self._events = deque(maxlen=128)
            self._batch = None
            self._track_token = b""
            self._batch_token = b""
            self._picture_token = b""
            self._artwork = None
            self._pending_artwork = None
            self._client_name = ""

    @property
    def client_name(self):
        with self._lock:
            return self._client_name

    @property
    def meta(self):
        with self._lock:
            return dict(self._meta) if self._meta else None

    @property
    def events(self):
        with self._lock:
            return [(seq, dict(meta)) for seq, meta in self._events]

    @property
    def artwork(self):
        with self._lock:
            return self._artwork

    def snapshot(self):
        with self._lock:
            if not self._meta:
                return None
            return {**self._meta, "artwork_id": self._artwork[0] if self._artwork else ""}

    def feed(self, chunk: bytes):
        # Reads are small; discard oversized/unframed input while retaining a
        # possible split '<item>' prefix. A bad item cannot poison later items.
        self._buffer.extend(chunk)
        while self._buffer:
            start = self._buffer.find(b"<item>")
            if start < 0:
                del self._buffer[:-5]
                return
            if start:
                del self._buffer[:start]
            end = self._buffer.find(b"</item>")
            next_start = self._buffer.find(b"<item>", 6)
            if next_start >= 0 and (end < 0 or next_start < end):
                del self._buffer[:next_start]
                continue
            if end < 0:
                if len(self._buffer) > MAX_ITEM_BYTES:
                    del self._buffer[:-5]
                return
            end += len(b"</item>")
            item = bytes(self._buffer[:end])
            del self._buffer[:end]
            if len(item) <= MAX_ITEM_BYTES:
                self._parse(item)

    def _parse(self, item):
        try:
            root = ET.fromstring(item)
            kind = int(root.findtext("type", ""), 16).to_bytes(4, "big").decode("ascii")
            code = int(root.findtext("code", ""), 16).to_bytes(4, "big").decode("ascii")
            length = int(root.findtext("length", ""))
            limit = MAX_ARTWORK_BYTES if (kind, code) == ("ssnc", "PICT") else 65536
            if not 0 <= length <= limit:
                return
            encoded = root.find("data")
            if encoded is not None and encoded.get("encoding") != "base64":
                return
            data = base64.b64decode(b"".join((root.findtext("data", "")).encode("ascii").split()),
                                    validate=True)
            if len(data) != length:
                return
        except (ET.ParseError, ValueError, OverflowError, UnicodeError, binascii.Error):
            return
        with self._lock:
            self._accept(kind, code, data)

    def _accept(self, kind, code, data):
        if kind == "core" and code in _FIELDS:
            if self._batch is not None:
                self._batch[_FIELDS[code]] = data.decode("utf-8", errors="replace").strip()
        elif kind == "ssnc":
            if code == "mdst":
                self._batch = {}
                self._batch_token = data
            elif code == "mden" and self._batch is not None:
                batch, self._batch = self._batch, None
                # Commit a complete transaction, never combine a new title
                # with the previous track's absent artist/album.
                if "title" not in batch:
                    return
                artist = batch.get("artist", "")
                title = _strip_artist_prefix(_strip_artist_suffix(batch["title"], artist), artist)
                meta = {"title": title, "artist": artist, "album": batch.get("album", ""),
                        "derived": _derive_title_from_artist(artist)}
                if self._meta != meta or (self._batch_token and self._track_token != self._batch_token):
                    self._artwork = None
                self._meta = meta if title else None
                self._track_token = self._batch_token
                pending = self._pending_artwork
                if pending and pending[0] == self._track_token:
                    self._artwork = pending[1]
                    self._pending_artwork = None
                self._sequence += 1
                self._events.append((self._sequence, meta))
            elif code == "pcst":
                self._picture_token = data
            elif code == "PICT":
                mime = ""
                if data.startswith(b"\xff\xd8\xff"):
                    mime = "image/jpeg"
                elif data.startswith(b"\x89PNG\r\n\x1a\n"):
                    mime = "image/png"
                if not data:
                    self._artwork = None
                    self._pending_artwork = None
                elif mime:
                    artwork = (hashlib.sha256(data).hexdigest(), mime, data)
                    if not self._picture_token or self._picture_token == self._track_token:
                        self._artwork = artwork
                    else:
                        # Pictures can precede their track metadata. Keep one
                        # pending picture; never show it on an unrelated track.
                        self._pending_artwork = (self._picture_token, artwork)
            elif code == "snam":
                self._client_name = data.decode("utf-8", errors="replace").strip()
            elif code == "pend":
                self.reset()
