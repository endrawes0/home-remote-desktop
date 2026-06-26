from __future__ import annotations

import argparse
import io
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from .common import (
    ANNOUNCE_TYPE,
    DEFAULT_TCP_PORT,
    DISCOVER_REQUEST,
    DISCOVERY_PORT,
    recv_packet,
    send_packet,
    socket_ipv4,
    summarize,
    write_json,
)


KEY_MAP = {
    "Return": "enter",
    "Escape": "esc",
    "BackSpace": "backspace",
    "Tab": "tab",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Prior": "pageup",
    "Next": "pagedown",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "space": "space",
    "Control_L": "ctrl",
    "Control_R": "ctrl",
    "Shift_L": "shift",
    "Shift_R": "shift",
    "Alt_L": "alt",
    "Alt_R": "alt",
}


@dataclass
class CaptureState:
    left: int
    top: int
    width: int
    height: int


@dataclass
class ServerProfile:
    output_path: str
    started_wall: float = field(default_factory=time.time)
    frame_count: int = 0
    bytes_sent: int = 0
    capture_ms: list[float] = field(default_factory=list)
    convert_resize_ms: list[float] = field(default_factory=list)
    encode_ms: list[float] = field(default_factory=list)
    send_ms: list[float] = field(default_factory=list)
    frame_total_ms: list[float] = field(default_factory=list)
    payload_bytes: list[float] = field(default_factory=list)
    image_width: int = 0
    image_height: int = 0
    screen_width: int = 0
    screen_height: int = 0
    quality: int = 0
    scale: float = 0.0
    fps_limit: int = 0

    def record(
        self,
        *,
        capture_ms: float,
        convert_resize_ms: float,
        encode_ms: float,
        send_ms: float,
        frame_total_ms: float,
        payload_size: int,
        image_width: int,
        image_height: int,
        state: CaptureState,
        quality: int,
        scale: float,
        fps_limit: int,
    ) -> None:
        self.frame_count += 1
        self.bytes_sent += payload_size
        self.capture_ms.append(capture_ms)
        self.convert_resize_ms.append(convert_resize_ms)
        self.encode_ms.append(encode_ms)
        self.send_ms.append(send_ms)
        self.frame_total_ms.append(frame_total_ms)
        self.payload_bytes.append(float(payload_size))
        self.image_width = image_width
        self.image_height = image_height
        self.screen_width = state.width
        self.screen_height = state.height
        self.quality = quality
        self.scale = scale
        self.fps_limit = fps_limit

    def write(self) -> None:
        elapsed = max(0.001, time.time() - self.started_wall)
        write_json(
            self.output_path,
            {
                "role": "server",
                "elapsed_seconds": elapsed,
                "frames": self.frame_count,
                "fps": self.frame_count / elapsed,
                "mbps": (self.bytes_sent * 8.0 / elapsed) / 1_000_000,
                "bytes_sent": self.bytes_sent,
                "screen": {"width": self.screen_width, "height": self.screen_height},
                "stream": {
                    "width": self.image_width,
                    "height": self.image_height,
                    "quality": self.quality,
                    "scale": self.scale,
                    "fps_limit": self.fps_limit,
                },
                "payload_bytes": summarize(self.payload_bytes),
                "capture_ms": summarize(self.capture_ms),
                "convert_resize_ms": summarize(self.convert_resize_ms),
                "encode_ms": summarize(self.encode_ms),
                "send_ms": summarize(self.send_ms),
                "frame_total_ms": summarize(self.frame_total_ms),
            },
        )


class RemoteDesktopServer:
    def __init__(
        self,
        name: str,
        passcode: str,
        host: str,
        port: int,
        fps: int,
        quality: int,
        scale: float,
        profile_output: str | None,
    ):
        self.name = name
        self.passcode = passcode
        self.host = host
        self.port = port
        self.fps = max(1, min(fps, 30))
        self.quality = max(35, min(quality, 95))
        self.scale = max(0.2, min(scale, 1.0))
        self.profile_output = profile_output
        self.stop_event = threading.Event()

    def start(self) -> None:
        discovery = threading.Thread(target=self._discovery_loop, daemon=True)
        discovery.start()
        self._tcp_loop()

    def _discovery_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        while not self.stop_event.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except OSError:
                break
            if data != DISCOVER_REQUEST:
                continue
            reply = {
                "type": ANNOUNCE_TYPE,
                "name": self.name,
                "port": self.port,
                "id": socket.gethostname(),
                "requires_passcode": True,
            }
            sock.sendto(json.dumps(reply, separators=(",", ":")).encode("utf-8"), addr)

    def _tcp_loop(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(5)
        print(f"Server: {self.name}")
        print(f"Address: {socket_ipv4()}:{self.port}")
        print(f"Discovery UDP port: {DISCOVERY_PORT}")
        print(f"Passcode: {self.passcode}")
        print("Waiting for a client. Press Ctrl+C to stop.")
        while not self.stop_event.is_set():
            client, addr = server.accept()
            print(f"Client connected from {addr[0]}:{addr[1]}")
            threading.Thread(target=self._handle_client, args=(client, addr), daemon=True).start()

    def _handle_client(self, client: socket.socket, addr: tuple[str, int]) -> None:
        alive: threading.Event | None = None
        stream: threading.Thread | None = None
        profile: ServerProfile | None = None
        try:
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            hello, _ = recv_packet(client)
            if hello.get("type") != "hello" or hello.get("passcode") != self.passcode:
                send_packet(client, {"type": "auth", "ok": False, "error": "bad passcode"})
                return

            mss, pyautogui = import_capture_modules()
            pyautogui.FAILSAFE = False
            with mss.mss() as screen:
                monitor = screen.monitors[1]
                state = CaptureState(monitor["left"], monitor["top"], monitor["width"], monitor["height"])

            send_packet(client, {"type": "auth", "ok": True, "screen_w": state.width, "screen_h": state.height})
            send_lock = threading.Lock()
            alive = threading.Event()
            alive.set()
            if self.profile_output:
                profile = ServerProfile(self.profile_output)
            stream = threading.Thread(
                target=self._stream_frames,
                args=(client, send_lock, alive, state, profile),
                daemon=True,
            )
            stream.start()
            self._input_loop(client, pyautogui, alive, state)
        except Exception as exc:
            print(f"Client {addr[0]} disconnected: {exc}")
        finally:
            if alive:
                alive.clear()
            try:
                client.close()
            except OSError:
                pass
            if stream:
                stream.join(timeout=2.0)
            if profile:
                profile.write()
                print(f"Wrote server profile to {profile.output_path}")

    def _stream_frames(
        self,
        client: socket.socket,
        send_lock: threading.Lock,
        alive: threading.Event,
        state: CaptureState,
        profile: ServerProfile | None,
    ) -> None:
        mss, _ = import_capture_modules()
        interval = 1.0 / self.fps
        frame = 0
        with mss.mss() as screen:
            monitor = {"left": state.left, "top": state.top, "width": state.width, "height": state.height}
            while alive.is_set():
                started = time.perf_counter()
                capture_started = time.perf_counter()
                shot = screen.grab(monitor)
                captured = time.perf_counter()
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                if self.scale != 1.0:
                    new_size = (max(1, int(image.width * self.scale)), max(1, int(image.height * self.scale)))
                    image = image.resize(new_size, Image.Resampling.BILINEAR)
                converted = time.perf_counter()
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=self.quality, optimize=True)
                encoded = time.perf_counter()
                payload = output.getvalue()
                packet = {
                    "type": "frame",
                    "frame": frame,
                    "server_wall_ns": time.time_ns(),
                    "screen_w": state.width,
                    "screen_h": state.height,
                    "image_w": image.width,
                    "image_h": image.height,
                    "format": "jpeg",
                }
                try:
                    send_started = time.perf_counter()
                    with send_lock:
                        send_packet(client, packet, payload)
                    sent = time.perf_counter()
                except OSError:
                    alive.clear()
                    return
                if profile:
                    profile.record(
                        capture_ms=(captured - capture_started) * 1000.0,
                        convert_resize_ms=(converted - captured) * 1000.0,
                        encode_ms=(encoded - converted) * 1000.0,
                        send_ms=(sent - send_started) * 1000.0,
                        frame_total_ms=(sent - started) * 1000.0,
                        payload_size=len(payload),
                        image_width=image.width,
                        image_height=image.height,
                        state=state,
                        quality=self.quality,
                        scale=self.scale,
                        fps_limit=self.fps,
                    )
                frame += 1
                elapsed = time.perf_counter() - started
                if elapsed < interval:
                    time.sleep(interval - elapsed)

    def _input_loop(self, client: socket.socket, pyautogui: Any, alive: threading.Event, state: CaptureState) -> None:
        while alive.is_set():
            message, _ = recv_packet(client)
            if message.get("type") != "input":
                continue
            event = message.get("event")
            if event in {"move", "down", "up", "click"}:
                x = state.left + int(float(message.get("nx", 0)) * state.width)
                y = state.top + int(float(message.get("ny", 0)) * state.height)
                button = message.get("button", "left")
                if event == "move":
                    pyautogui.moveTo(x, y)
                elif event == "down":
                    pyautogui.mouseDown(x, y, button=button)
                elif event == "up":
                    pyautogui.mouseUp(x, y, button=button)
                elif event == "click":
                    pyautogui.click(x, y, button=button)
            elif event == "wheel":
                pyautogui.scroll(int(message.get("delta", 0)))
            elif event in {"key_down", "key_up"}:
                key = normalize_key(message)
                if key:
                    if event == "key_down":
                        pyautogui.keyDown(key)
                    else:
                        pyautogui.keyUp(key)


def normalize_key(message: dict[str, Any]) -> str | None:
    char = message.get("char") or ""
    keysym = message.get("keysym") or ""
    if len(char) == 1 and char.isprintable():
        return char.lower()
    if keysym.startswith("F") and keysym[1:].isdigit():
        return keysym.lower()
    return KEY_MAP.get(keysym)


def import_capture_modules() -> tuple[Any, Any]:
    try:
        import mss
        import pyautogui
    except ImportError as exc:
        raise SystemExit(
            "Missing runtime dependency. Run: py -m pip install -r requirements.txt"
        ) from exc
    return mss, pyautogui


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Home Remote Desktop server")
    parser.add_argument("--name", default=socket.gethostname(), help="Name shown in client discovery")
    parser.add_argument("--passcode", default="", help="Connection passcode. Generated when omitted.")
    parser.add_argument("--host", default="0.0.0.0", help="TCP bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_TCP_PORT, help="TCP port")
    parser.add_argument("--fps", type=int, default=12, help="Maximum frames per second")
    parser.add_argument("--quality", type=int, default=70, help="JPEG quality 35-95")
    parser.add_argument("--scale", type=float, default=0.75, help="Stream scale 0.2-1.0")
    parser.add_argument("--profile-output", default=None, help="Write server-side profiling JSON after a client disconnects")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    passcode = args.passcode or f"{secrets.randbelow(900000) + 100000}"
    RemoteDesktopServer(
        args.name,
        passcode,
        args.host,
        args.port,
        args.fps,
        args.quality,
        args.scale,
        args.profile_output,
    ).start()


if __name__ == "__main__":
    main()
