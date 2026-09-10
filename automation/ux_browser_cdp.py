from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
from urllib.parse import urlsplit


class BrowserCDPError(RuntimeError):
    pass


class WebSocketClient:
    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise BrowserCDPError(f"invalid DevTools websocket URL: {url!r}")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        try:
            raw = socket.create_connection((parsed.hostname, port), timeout=timeout_seconds)
        except OSError as exc:
            raise BrowserCDPError(f"cannot connect to DevTools websocket: {exc}") from exc
        if parsed.scheme == "wss":
            context = ssl.create_default_context()
            self._socket = context.wrap_socket(raw, server_hostname=parsed.hostname)
        else:
            self._socket = raw
        self._socket.settimeout(timeout_seconds)
        self._buffer = bytearray()
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        self._handshake(parsed.hostname, port, path)

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except (BrowserCDPError, OSError):
            pass
        try:
            self._socket.close()
        except OSError:
            pass

    def send_json(self, value: dict[str, object]) -> None:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._send_frame(0x1, payload)

    def recv_json(self, *, timeout_seconds: float | None = None) -> dict[str, object]:
        previous = self._socket.gettimeout()
        if timeout_seconds is not None:
            self._socket.settimeout(timeout_seconds)
        try:
            fragments = bytearray()
            active_opcode = 0
            while True:
                fin, opcode, payload = self._read_frame()
                if opcode == 0x8:
                    raise BrowserCDPError("DevTools websocket closed unexpectedly")
                if opcode == 0x9:
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode in {0x1, 0x2}:
                    active_opcode = opcode
                    fragments.extend(payload)
                elif opcode == 0x0 and active_opcode:
                    fragments.extend(payload)
                else:
                    continue
                if not fin:
                    continue
                if active_opcode != 0x1:
                    fragments.clear()
                    active_opcode = 0
                    continue
                try:
                    value = json.loads(fragments.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BrowserCDPError("DevTools websocket returned malformed JSON") from exc
                if not isinstance(value, dict):
                    raise BrowserCDPError("DevTools websocket returned non-object JSON")
                return value
        except socket.timeout as exc:
            raise BrowserCDPError("timed out waiting for DevTools response") from exc
        except OSError as exc:
            raise BrowserCDPError(f"DevTools websocket I/O failed: {exc}") from exc
        finally:
            if timeout_seconds is not None:
                self._socket.settimeout(previous)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Origin: http://{host}:{port}\r\n"
            "\r\n"
        ).encode("ascii")
        try:
            self._socket.sendall(request)
            response = self._read_until(b"\r\n\r\n", limit=64 * 1024)
        except OSError as exc:
            raise BrowserCDPError(f"DevTools websocket handshake failed: {exc}") from exc
        head = response.decode("iso-8859-1", errors="replace")
        lines = head.split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            raise BrowserCDPError(f"DevTools websocket upgrade was rejected: {lines[0] if lines else head}")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().casefold()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept", "") != expected:
            raise BrowserCDPError("DevTools websocket handshake returned an invalid accept key")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        try:
            self._socket.sendall(bytes(header) + masked)
        except OSError as exc:
            raise BrowserCDPError(f"cannot send DevTools websocket frame: {exc}") from exc

    def _read_frame(self) -> tuple[bool, int, bytes]:
        header = self._read_exact(2)
        first, second = header[0], header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if length > 64 * 1024 * 1024:
            raise BrowserCDPError("DevTools websocket frame exceeds safety limit")
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload

    def _read_until(self, marker: bytes, *, limit: int) -> bytes:
        while marker not in self._buffer:
            if len(self._buffer) >= limit:
                raise BrowserCDPError("DevTools websocket handshake exceeded safety limit")
            chunk = self._socket.recv(4096)
            if not chunk:
                raise BrowserCDPError("DevTools websocket closed during handshake")
            self._buffer.extend(chunk)
        index = self._buffer.index(marker) + len(marker)
        value = bytes(self._buffer[:index])
        del self._buffer[:index]
        return value

    def _read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self._socket.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise BrowserCDPError("DevTools websocket closed unexpectedly")
            self._buffer.extend(chunk)
        value = bytes(self._buffer[:size])
        del self._buffer[:size]
        return value


class CDPSession:
    def __init__(self, websocket: WebSocketClient) -> None:
        self.websocket = websocket
        self._next_id = 1
        self._ignored_ids: set[int] = set()

    def close(self) -> None:
        self.websocket.close()

    def command(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        timeout_seconds: float = 15.0,
        event_handler=None,
    ) -> dict[str, object]:
        request_id = self._allocate_id()
        self.websocket.send_json(
            {
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        while True:
            message = self.websocket.recv_json(timeout_seconds=timeout_seconds)
            response_id = message.get("id")
            if isinstance(response_id, int):
                if response_id in self._ignored_ids:
                    self._ignored_ids.discard(response_id)
                    continue
                if response_id != request_id:
                    continue
                if "error" in message:
                    raise BrowserCDPError(
                        f"DevTools command {method} failed: {json.dumps(message['error'], ensure_ascii=False)}"
                    )
                result = message.get("result", {})
                return result if isinstance(result, dict) else {}
            if event_handler is not None:
                event_handler(message, self)

    def fire_and_forget(self, method: str, params: dict[str, object] | None = None) -> None:
        request_id = self._allocate_id()
        self._ignored_ids.add(request_id)
        self.websocket.send_json(
            {
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )

    def _allocate_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id
