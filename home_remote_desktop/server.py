from __future__ import annotations

import argparse
import io
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass
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


class RemoteDesktopServer:
    def __init__(self, name: str, passcode: str, host: str, port: int, fps: int, quality: int, scale: float):
        self.name = name
        self.passcode = passcode
        self.host = host
        self.port = port
        self.fps = max(1, min(fps, 30))
        self.quality = max(35, min(quality, 95))
        self.scale = max(0.2, min(scale, 1.0))
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
            stream = threading.Thread(target=self._stream_frames, args=(client, send_lock, alive, state), daemon=True)
            stream.start()
            self._input_loop(client, pyautogui, alive, state)
        except Exception as exc:
            print(f"Client {addr[0]} disconnected: {exc}")
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _stream_frames(
        self,
        client: socket.socket,
        send_lock: threading.Lock,
        alive: threading.Event,
        state: CaptureState,
    ) -> None:
        mss, _ = import_capture_modules()
        interval = 1.0 / self.fps
        frame = 0
        with mss.mss() as screen:
            monitor = {"left": state.left, "top": state.top, "width": state.width, "height": state.height}
            while alive.is_set():
                started = time.monotonic()
                shot = screen.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                if self.scale != 1.0:
                    new_size = (max(1, int(image.width * self.scale)), max(1, int(image.height * self.scale)))
                    image = image.resize(new_size, Image.Resampling.BILINEAR)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=self.quality, optimize=True)
                packet = {
                    "type": "frame",
                    "frame": frame,
                    "screen_w": state.width,
                    "screen_h": state.height,
                    "image_w": image.width,
                    "image_h": image.height,
                    "format": "jpeg",
                }
                try:
                    with send_lock:
                        send_packet(client, packet, output.getvalue())
                except OSError:
                    alive.clear()
                    return
                frame += 1
                elapsed = time.monotonic() - started
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    passcode = args.passcode or f"{secrets.randbelow(900000) + 100000}"
    RemoteDesktopServer(args.name, passcode, args.host, args.port, args.fps, args.quality, args.scale).start()


if __name__ == "__main__":
    main()
