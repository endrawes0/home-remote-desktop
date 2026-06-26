from __future__ import annotations

import argparse
import io
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any

from PIL import Image, ImageTk

from .common import (
    ANNOUNCE_TYPE,
    DEFAULT_TCP_PORT,
    DISCOVER_REQUEST,
    DISCOVERY_PORT,
    recv_packet,
    send_packet,
    summarize,
    write_json,
)


@dataclass
class ServerInfo:
    name: str
    host: str
    port: int
    server_id: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"


@dataclass
class ClientProfile:
    host: str
    port: int
    seconds: float
    output_path: str
    started_wall: float = field(default_factory=time.time)
    frames: int = 0
    bytes_received: int = 0
    receive_ms: list[float] = field(default_factory=list)
    decode_ms: list[float] = field(default_factory=list)
    end_to_end_ms: list[float] = field(default_factory=list)
    inter_frame_ms: list[float] = field(default_factory=list)
    payload_bytes: list[float] = field(default_factory=list)
    image_width: int = 0
    image_height: int = 0
    screen_width: int = 0
    screen_height: int = 0
    first_frame_wall: float | None = None
    last_frame_wall: float | None = None

    def record(
        self,
        *,
        header: dict[str, Any],
        payload_size: int,
        receive_ms: float,
        decode_ms: float,
        received_wall_ns: int,
    ) -> None:
        now = time.time()
        if self.last_frame_wall is not None:
            self.inter_frame_ms.append((now - self.last_frame_wall) * 1000.0)
        self.first_frame_wall = self.first_frame_wall or now
        self.last_frame_wall = now
        self.frames += 1
        self.bytes_received += payload_size
        self.receive_ms.append(receive_ms)
        self.decode_ms.append(decode_ms)
        self.payload_bytes.append(float(payload_size))
        self.image_width = int(header.get("image_w", 0))
        self.image_height = int(header.get("image_h", 0))
        self.screen_width = int(header.get("screen_w", 0))
        self.screen_height = int(header.get("screen_h", 0))
        server_wall_ns = int(header.get("server_wall_ns") or 0)
        if server_wall_ns:
            self.end_to_end_ms.append((received_wall_ns - server_wall_ns) / 1_000_000.0)

    def write(self) -> None:
        elapsed = max(0.001, time.time() - self.started_wall)
        active_elapsed = elapsed
        if self.first_frame_wall is not None and self.last_frame_wall is not None:
            active_elapsed = max(0.001, self.last_frame_wall - self.first_frame_wall)
        write_json(
            self.output_path,
            {
                "role": "client",
                "host": self.host,
                "port": self.port,
                "requested_seconds": self.seconds,
                "elapsed_seconds": elapsed,
                "active_elapsed_seconds": active_elapsed,
                "frames": self.frames,
                "fps": self.frames / active_elapsed,
                "mbps": (self.bytes_received * 8.0 / active_elapsed) / 1_000_000,
                "bytes_received": self.bytes_received,
                "screen": {"width": self.screen_width, "height": self.screen_height},
                "stream": {"width": self.image_width, "height": self.image_height},
                "payload_bytes": summarize(self.payload_bytes),
                "frame_wait_receive_ms": summarize(self.receive_ms),
                "decode_ms": summarize(self.decode_ms),
                "end_to_end_ms": summarize(self.end_to_end_ms),
                "inter_frame_ms": summarize(self.inter_frame_ms),
            },
        )


@dataclass
class PairProfile:
    name: str
    server_config: dict[str, Any]
    started_wall: float = field(default_factory=time.time)
    frames: int = 0
    bytes_received: int = 0
    receive_ms: list[float] = field(default_factory=list)
    decode_ms: list[float] = field(default_factory=list)
    end_to_end_ms: list[float] = field(default_factory=list)
    inter_frame_ms: list[float] = field(default_factory=list)
    payload_bytes: list[float] = field(default_factory=list)
    server_capture_ms: list[float] = field(default_factory=list)
    server_convert_resize_ms: list[float] = field(default_factory=list)
    server_encode_ms: list[float] = field(default_factory=list)
    server_frame_ms: list[float] = field(default_factory=list)
    changed_tiles: list[float] = field(default_factory=list)
    total_tiles: list[float] = field(default_factory=list)
    capture_backend: str = ""
    jpeg_backend: str = ""
    first_frame_wall: float | None = None
    last_frame_wall: float | None = None
    stream: dict[str, int] = field(default_factory=dict)

    def record(self, header: dict[str, Any], payload_size: int, receive_ms: float, decode_ms: float, received_wall_ns: int) -> None:
        now = time.time()
        if self.last_frame_wall is not None:
            self.inter_frame_ms.append((now - self.last_frame_wall) * 1000.0)
        self.first_frame_wall = self.first_frame_wall or now
        self.last_frame_wall = now
        self.frames += 1
        self.bytes_received += payload_size
        self.receive_ms.append(receive_ms)
        self.decode_ms.append(decode_ms)
        self.payload_bytes.append(float(payload_size))
        self.stream = {
            "width": int(header.get("image_w", 0)),
            "height": int(header.get("image_h", 0)),
        }
        self.capture_backend = str(header.get("capture_backend") or self.capture_backend)
        self.jpeg_backend = str(header.get("jpeg_backend") or self.jpeg_backend)
        server_wall_ns = int(header.get("server_wall_ns") or 0)
        if server_wall_ns:
            self.end_to_end_ms.append((received_wall_ns - server_wall_ns) / 1_000_000.0)
        for key, target in (
            ("capture_ms", self.server_capture_ms),
            ("convert_resize_ms", self.server_convert_resize_ms),
            ("encode_ms", self.server_encode_ms),
            ("server_frame_ms", self.server_frame_ms),
            ("changed_tiles", self.changed_tiles),
            ("total_tiles", self.total_tiles),
        ):
            if key in header:
                target.append(float(header[key]))

    def to_result(self) -> dict[str, Any]:
        elapsed = max(0.001, time.time() - self.started_wall)
        active_elapsed = elapsed
        if self.first_frame_wall is not None and self.last_frame_wall is not None:
            active_elapsed = max(0.001, self.last_frame_wall - self.first_frame_wall)
        client_data = {
            "frames": self.frames,
            "fps": self.frames / active_elapsed,
            "mbps": (self.bytes_received * 8.0 / active_elapsed) / 1_000_000,
            "bytes_received": self.bytes_received,
            "payload_bytes": summarize(self.payload_bytes),
            "frame_wait_receive_ms": summarize(self.receive_ms),
            "decode_ms": summarize(self.decode_ms),
            "end_to_end_ms": summarize(self.end_to_end_ms),
            "inter_frame_ms": summarize(self.inter_frame_ms),
            "stream": self.stream,
        }
        server_data = {
            "capture_backend": self.capture_backend,
            "jpeg_backend": self.jpeg_backend,
            "capture_ms": summarize(self.server_capture_ms),
            "convert_resize_ms": summarize(self.server_convert_resize_ms),
            "encode_ms": summarize(self.server_encode_ms),
            "frame_ms": summarize(self.server_frame_ms),
            "changed_tiles": summarize(self.changed_tiles),
            "total_tiles": summarize(self.total_tiles),
        }
        return {
            "name": self.name,
            "ok": self.frames > 0,
            "server_config": self.server_config,
            "client": client_data,
            "server": server_data,
            "score": pair_config_score(client_data, server_data),
        }


class RemoteDesktopClient(tk.Tk):
    def __init__(self, host: str | None, port: int, passcode: str | None, input_debug: bool = False):
        super().__init__()
        self.title("Home Remote Desktop")
        self.geometry("1100x720")
        self.minsize(640, 420)

        self.host = host
        self.port = port
        self.passcode = passcode
        self.sock: socket.socket | None = None
        self.send_lock = threading.Lock()
        self.frame_queue: queue.Queue[tuple[dict[str, Any], bytes]] = queue.Queue(maxsize=2)
        self.current_photo: ImageTk.PhotoImage | None = None
        self.desktop_image: Image.Image | None = None
        self.image_size = (1, 1)
        self.screen_size = (1, 1)
        self.connected = False
        self.input_debug = input_debug
        self.fullscreen = False
        self.pointer_down: dict[str, tuple[float, float]] = {}
        self.pointer_dragging: set[str] = set()
        self.pointer_threshold = 4

        self._build_ui()
        self.after(50, self._drain_frames)
        if self.host:
            self.after(100, self._connect_prompted)
        else:
            self.after(100, self.discover)

    def _build_ui(self) -> None:
        self.toolbar = ttk.Frame(self, padding=(8, 6))
        self.toolbar.pack(side=tk.TOP, fill=tk.X)

        self.server_var = tk.StringVar()
        self.servers: list[ServerInfo] = []
        self.server_combo = ttk.Combobox(self.toolbar, textvariable=self.server_var, state="readonly", width=48)
        self.server_combo.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(self.toolbar, text="Discover", command=self.discover).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(self.toolbar, text="Connect", command=self._connect_selected).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(self.toolbar, text="Disconnect", command=self.disconnect).pack(side=tk.LEFT, padx=(0, 6))
        self.fullscreen_button = ttk.Button(self.toolbar, text="Fullscreen", command=self.toggle_fullscreen)
        self.fullscreen_button.pack(side=tk.LEFT, padx=(0, 12))

        self.status_var = tk.StringVar(value="Discovering servers...")
        ttk.Label(self.toolbar, textvariable=self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.canvas = tk.Canvas(self, bg="#101010", highlightthickness=0, takefocus=True)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._redraw_current())
        self.canvas.bind("<Motion>", self._mouse_move)
        self.canvas.bind("<ButtonPress-1>", lambda e: self._mouse_press(e, "left"))
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._mouse_release(e, "left"))
        self.canvas.bind("<ButtonPress-2>", lambda e: self._mouse_press(e, "middle"))
        self.canvas.bind("<ButtonRelease-2>", lambda e: self._mouse_release(e, "middle"))
        self.canvas.bind("<ButtonPress-3>", lambda e: self._mouse_press(e, "right"))
        self.canvas.bind("<ButtonRelease-3>", lambda e: self._mouse_release(e, "right"))
        self.canvas.bind("<MouseWheel>", self._mouse_wheel)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.focus_set())
        self.bind_all("<KeyPress>", self._key_down)
        self.bind_all("<KeyRelease>", self._key_up)

    def discover(self) -> None:
        self.status_var.set("Searching local network...")
        threading.Thread(target=self._discover_worker, daemon=True).start()

    def _discover_worker(self) -> None:
        found: dict[tuple[str, int], ServerInfo] = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.35)
        try:
            for target in ("255.255.255.255", "<broadcast>"):
                try:
                    sock.sendto(DISCOVER_REQUEST, (target, DISCOVERY_PORT))
                except OSError:
                    pass
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                try:
                    message = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if message.get("type") != ANNOUNCE_TYPE:
                    continue
                port = int(message.get("port", DEFAULT_TCP_PORT))
                found[(addr[0], port)] = ServerInfo(
                    name=str(message.get("name") or addr[0]),
                    host=addr[0],
                    port=port,
                    server_id=str(message.get("id") or addr[0]),
                )
        finally:
            sock.close()
        self.after(0, lambda: self._set_servers(list(found.values())))

    def _set_servers(self, servers: list[ServerInfo]) -> None:
        self.servers = sorted(servers, key=lambda item: item.label.lower())
        self.server_combo["values"] = [server.label for server in self.servers]
        if self.servers:
            self.server_combo.current(0)
            self.status_var.set(f"Found {len(self.servers)} server(s).")
        else:
            self.status_var.set("No servers found. Check firewall/network settings or enter --host.")

    def _connect_selected(self) -> None:
        index = self.server_combo.current()
        if index < 0 or index >= len(self.servers):
            messagebox.showinfo("Home Remote Desktop", "No discovered server is selected.")
            return
        server = self.servers[index]
        self.host, self.port = server.host, server.port
        self._connect_prompted()

    def _connect_prompted(self) -> None:
        if not self.host:
            return
        if not self.passcode:
            self.passcode = simpledialog.askstring("Passcode", f"Passcode for {self.host}:", show="*")
            if not self.passcode:
                return
        self.disconnect()
        self.status_var.set(f"Connecting to {self.host}:{self.port}...")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=8)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            send_packet(sock, {"type": "hello", "passcode": self.passcode, "client": socket.gethostname()})
            auth, _ = recv_packet(sock)
            if not auth.get("ok"):
                raise ConnectionError(str(auth.get("error") or "authentication failed"))
            self.sock = sock
            self.connected = True
            self.screen_size = (int(auth.get("screen_w", 1)), int(auth.get("screen_h", 1)))
            self.after(0, lambda: self.status_var.set(f"Connected to {self.host}:{self.port}"))
            self._receive_loop(sock)
        except Exception as exc:
            self.connected = False
            message = str(exc)
            self.after(0, lambda: self.status_var.set(f"Disconnected: {message}"))
            try:
                if self.sock:
                    self.sock.close()
            except OSError:
                pass

    def _receive_loop(self, sock: socket.socket) -> None:
        while self.connected:
            header, payload = recv_packet(sock)
            if header.get("type") != "frame":
                continue
            while self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    break
            self.frame_queue.put((header, payload))

    def _drain_frames(self) -> None:
        latest: tuple[dict[str, Any], bytes] | None = None
        try:
            while True:
                latest = self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        if latest:
            header, payload = latest
            self.screen_size = (int(header["screen_w"]), int(header["screen_h"]))
            image = apply_frame_payload(header, payload, self.desktop_image)
            self.desktop_image = image
            self.image_size = image.size
            self._show_image(image)
        self.after(33, self._drain_frames)

    def _show_image(self, image: Image.Image) -> None:
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        scale = min(canvas_w / image.width, canvas_h / image.height)
        draw_size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        if draw_size != image.size:
            image = image.resize(draw_size, Image.Resampling.BILINEAR)
        self.current_photo = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        x = (canvas_w - draw_size[0]) // 2
        y = (canvas_h - draw_size[1]) // 2
        self.canvas.create_image(x, y, image=self.current_photo, anchor=tk.NW, tags=("desktop",))
        self.canvas.configure(scrollregion=(x, y, x + draw_size[0], y + draw_size[1]))

    def _redraw_current(self) -> None:
        if self.desktop_image is not None:
            self._show_image(self.desktop_image)

    def _normalized_point(self, event: tk.Event[Any]) -> tuple[float, float] | None:
        bbox = self.canvas.bbox("desktop")
        if not bbox:
            return None
        left, top, right, bottom = bbox
        if event.x < left or event.x > right or event.y < top or event.y > bottom:
            return None
        return ((event.x - left) / max(1, right - left), (event.y - top) / max(1, bottom - top))

    def _send_input(self, message: dict[str, Any]) -> None:
        if not self.connected or not self.sock:
            return
        try:
            with self.send_lock:
                send_packet(self.sock, {"type": "input", **message})
            if self.input_debug:
                print(f"input {message}", flush=True)
        except OSError:
            self.disconnect()

    def _mouse_move(self, event: tk.Event[Any]) -> None:
        point = self._normalized_point(event)
        if not point:
            return
        for button, start in list(self.pointer_down.items()):
            if button in self.pointer_dragging:
                continue
            dx = event.x - start[0]
            dy = event.y - start[1]
            if (dx * dx + dy * dy) >= self.pointer_threshold * self.pointer_threshold:
                self.pointer_dragging.add(button)
                self._send_input({"event": "down", "button": button, "nx": point[0], "ny": point[1]})
        self._send_input({"event": "move", "nx": point[0], "ny": point[1]})

    def _mouse_press(self, event: tk.Event[Any], button: str) -> None:
        self.canvas.focus_set()
        self.pointer_down[button] = (event.x, event.y)
        self.pointer_dragging.discard(button)
        point = self._normalized_point(event)
        if point:
            self._send_input({"event": "move", "nx": point[0], "ny": point[1]})

    def _mouse_release(self, event: tk.Event[Any], button: str) -> None:
        self.canvas.focus_set()
        point = self._normalized_point(event)
        dragging = button in self.pointer_dragging
        self.pointer_down.pop(button, None)
        self.pointer_dragging.discard(button)
        if not point:
            return
        if dragging:
            self._send_input({"event": "up", "button": button, "nx": point[0], "ny": point[1]})
        else:
            self._send_input({"event": "click", "button": button, "nx": point[0], "ny": point[1]})

    def _mouse_wheel(self, event: tk.Event[Any]) -> None:
        delta = 1 if event.delta > 0 else -1
        self._send_input({"event": "wheel", "delta": delta * 5})

    def _key_down(self, event: tk.Event[Any]) -> str | None:
        if event.keysym == "F11":
            self.toggle_fullscreen()
            return "break"
        if event.keysym == "Escape" and self.fullscreen:
            self.toggle_fullscreen(False)
            return "break"
        if len(event.char) == 1 and event.char.isprintable():
            self._send_input({"event": "text", "text": event.char})
            return "break"
        self._send_input({"event": "key_down", "keysym": event.keysym, "char": event.char})
        return None

    def _key_up(self, event: tk.Event[Any]) -> str | None:
        if event.keysym in {"F11", "Escape"}:
            return "break"
        if len(event.char) == 1 and event.char.isprintable():
            return "break"
        self._send_input({"event": "key_up", "keysym": event.keysym, "char": event.char})
        return None

    def toggle_fullscreen(self, enabled: bool | None = None) -> str:
        self.fullscreen = (not self.fullscreen) if enabled is None else enabled
        self.attributes("-fullscreen", self.fullscreen)
        if self.fullscreen:
            self.toolbar.pack_forget()
            self.fullscreen_button.configure(text="Exit Fullscreen")
        else:
            self.toolbar.pack(side=tk.TOP, fill=tk.X, before=self.canvas)
            self.fullscreen_button.configure(text="Fullscreen")
        self.canvas.focus_set()
        return "break"

    def _escape(self, _event: tk.Event[Any]) -> str | None:
        if self.fullscreen:
            self.toggle_fullscreen(False)
            return "break"
        return None

    def disconnect(self) -> None:
        self.connected = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Home Remote Desktop client")
    parser.add_argument("--host", default=None, help="Server IP or hostname. Omit to use discovery.")
    parser.add_argument("--port", type=int, default=DEFAULT_TCP_PORT, help="Server TCP port")
    parser.add_argument("--passcode", default=None, help="Server passcode")
    parser.add_argument("--profile-seconds", type=float, default=0.0, help="Run headless receive/decode profiling for N seconds")
    parser.add_argument("--profile-output", default="client-profile.json", help="Profiling JSON output path")
    parser.add_argument("--profile-config-sweep", action="store_true", help="Run local server/client profiling across candidate configs")
    parser.add_argument("--profile-config-output", default="profile-recommendation.json", help="Config sweep recommendation JSON path")
    parser.add_argument("--profile-config-dir", default="profile-config-results", help="Directory for per-config profiler JSON/logs")
    parser.add_argument("--profile-config-seconds", type=float, default=6.0, help="Seconds to profile each config")
    parser.add_argument("--pair-profile-sweep", action="store_true", help="Profile multiple configs against an already-running remote server")
    parser.add_argument("--pair-profile-output", default="pair-profile-recommendation.json", help="Pair profile recommendation JSON path")
    parser.add_argument("--pair-profile-seconds", type=float, default=6.0, help="Seconds to test each pair profile candidate")
    parser.add_argument("--input-debug", action="store_true", help="Print each mouse/keyboard input packet sent by the GUI client")
    return parser.parse_args()


def run_profile(host: str, port: int, passcode: str, seconds: float, output_path: str) -> None:
    profile = ClientProfile(host=host, port=port, seconds=seconds, output_path=output_path)
    sock = socket.create_connection((host, port), timeout=8)
    desktop_image: Image.Image | None = None
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        send_packet(sock, {"type": "hello", "passcode": passcode, "client": socket.gethostname(), "profile": True})
        auth, _ = recv_packet(sock)
        if not auth.get("ok"):
            raise ConnectionError(str(auth.get("error") or "authentication failed"))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            receive_started = time.perf_counter()
            header, payload = recv_packet(sock)
            received = time.perf_counter()
            received_wall_ns = time.time_ns()
            if header.get("type") != "frame":
                continue
            decode_started = time.perf_counter()
            desktop_image = apply_frame_payload(header, payload, desktop_image)
            decoded = time.perf_counter()
            profile.record(
                header=header,
                payload_size=len(payload),
                receive_ms=(received - receive_started) * 1000.0,
                decode_ms=(decoded - decode_started) * 1000.0,
                received_wall_ns=received_wall_ns,
            )
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        profile.write()


def apply_frame_payload(header: dict[str, Any], payload: bytes, previous: Image.Image | None) -> Image.Image:
    mode = header.get("mode", "full")
    if mode == "full":
        image = Image.open(io.BytesIO(payload))
        image.load()
        return image.convert("RGB")

    width = int(header.get("image_w", 1))
    height = int(header.get("image_h", 1))
    if previous is None or previous.size != (width, height):
        base = Image.new("RGB", (width, height))
    else:
        base = previous.copy()

    offset = 0
    for tile in header.get("tiles", []):
        size = int(tile["size"])
        tile_payload = payload[offset : offset + size]
        offset += size
        tile_image = Image.open(io.BytesIO(tile_payload))
        tile_image.load()
        base.paste(tile_image.convert("RGB"), (int(tile["x"]), int(tile["y"])))
    return base


def candidate_configs() -> list[dict[str, Any]]:
    return [
        {
            "name": "balanced-full",
            "server_args": ["--fps", "20", "--quality", "70", "--scale", "0.75", "--delta-mode", "off", "--no-jpeg-optimize"],
        },
        {
            "name": "balanced-full-optimized-jpeg",
            "server_args": ["--fps", "20", "--quality", "70", "--scale", "0.75", "--delta-mode", "off", "--jpeg-optimize"],
        },
        {
            "name": "fast-full",
            "server_args": ["--fps", "20", "--quality", "60", "--scale", "0.5", "--delta-mode", "off", "--no-jpeg-optimize"],
        },
        {
            "name": "fast-delta",
            "server_args": [
                "--fps",
                "20",
                "--quality",
                "60",
                "--scale",
                "0.5",
                "--delta-mode",
                "tiles",
                "--tile-size",
                "384",
                "--full-frame-interval",
                "90",
                "--no-jpeg-optimize",
            ],
        },
        {
            "name": "auto-backends-delta",
            "server_args": [
                "--fps",
                "20",
                "--quality",
                "60",
                "--scale",
                "0.5",
                "--capture-backend",
                "auto",
                "--jpeg-backend",
                "auto",
                "--delta-mode",
                "tiles",
                "--tile-size",
                "384",
                "--full-frame-interval",
                "90",
                "--no-jpeg-optimize",
            ],
        },
    ]


def pair_candidate_configs() -> list[dict[str, Any]]:
    return [
        {
            "name": "balanced-full",
            "fps": 20,
            "quality": 70,
            "scale": 0.75,
            "jpeg_backend": "pillow",
            "jpeg_optimize": False,
            "delta_mode": "off",
            "tile_size": 384,
            "full_frame_interval": 90,
        },
        {
            "name": "balanced-full-optimized-jpeg",
            "fps": 20,
            "quality": 70,
            "scale": 0.75,
            "jpeg_backend": "pillow",
            "jpeg_optimize": True,
            "delta_mode": "off",
            "tile_size": 384,
            "full_frame_interval": 90,
        },
        {
            "name": "fast-full",
            "fps": 20,
            "quality": 60,
            "scale": 0.5,
            "jpeg_backend": "pillow",
            "jpeg_optimize": False,
            "delta_mode": "off",
            "tile_size": 384,
            "full_frame_interval": 90,
        },
        {
            "name": "fast-delta",
            "fps": 20,
            "quality": 60,
            "scale": 0.5,
            "jpeg_backend": "pillow",
            "jpeg_optimize": False,
            "delta_mode": "tiles",
            "tile_size": 384,
            "full_frame_interval": 90,
        },
        {
            "name": "turbojpeg-full",
            "fps": 20,
            "quality": 60,
            "scale": 0.5,
            "jpeg_backend": "turbojpeg",
            "jpeg_optimize": False,
            "delta_mode": "off",
            "tile_size": 384,
            "full_frame_interval": 90,
        },
        {
            "name": "turbojpeg-delta",
            "fps": 20,
            "quality": 60,
            "scale": 0.5,
            "jpeg_backend": "turbojpeg",
            "jpeg_optimize": False,
            "delta_mode": "tiles",
            "tile_size": 384,
            "full_frame_interval": 90,
        },
        {
            "name": "auto-encoder-delta",
            "fps": 20,
            "quality": 60,
            "scale": 0.5,
            "jpeg_backend": "auto",
            "jpeg_optimize": False,
            "delta_mode": "tiles",
            "tile_size": 384,
            "full_frame_interval": 90,
        },
    ]


def run_pair_profile_sweep(host: str, port: int, passcode: str, seconds: float, output_path: str) -> None:
    sock = socket.create_connection((host, port), timeout=8)
    desktop_image: Image.Image | None = None
    results: list[dict[str, Any]] = []
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        send_packet(sock, {"type": "hello", "passcode": passcode, "client": socket.gethostname(), "pair_profile": True})
        auth, _ = recv_packet(sock)
        if not auth.get("ok"):
            raise ConnectionError(str(auth.get("error") or "authentication failed"))
        for config in pair_candidate_configs():
            send_packet(sock, {"type": "input", "event": "set_stream_config", **config})
            desktop_image = None
            deadline = time.monotonic() + seconds
            warmup_frames = 2
            profile = PairProfile(config["name"], config)
            while time.monotonic() < deadline:
                receive_started = time.perf_counter()
                header, payload = recv_packet(sock)
                received = time.perf_counter()
                received_wall_ns = time.time_ns()
                if header.get("type") != "frame":
                    continue
                decode_started = time.perf_counter()
                desktop_image = apply_frame_payload(header, payload, desktop_image)
                decoded = time.perf_counter()
                if header.get("config_name") != config["name"]:
                    continue
                if warmup_frames > 0:
                    warmup_frames -= 1
                    continue
                profile.record(
                    header,
                    len(payload),
                    (received - receive_started) * 1000.0,
                    (decoded - decode_started) * 1000.0,
                    received_wall_ns,
                )
            results.append(profile.to_result())
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    successful = [result for result in results if result.get("ok")]
    best = max(successful, key=lambda item: item["score"]) if successful else None
    recommendation: dict[str, Any] = {
        "role": "pair-profile-recommendation",
        "host": host,
        "port": port,
        "tested_seconds_per_config": seconds,
        "results": results,
        "recommended": None,
    }
    if best:
        flags = config_to_server_flags(best["server_config"])
        recommendation["recommended"] = {
            "name": best["name"],
            "score": best["score"],
            "server_command": f".\\run-server.bat --passcode {passcode} {flags}",
            "client_command": f".\\run-client.bat --host {host}",
            "pair_profile_command": (
                f".\\run-client.bat --host {host} --passcode {passcode} "
                f"--pair-profile-sweep --pair-profile-seconds {seconds:g} --pair-profile-output {output_path}"
            ),
            "why": "Chosen from an active client/server connection using client decode, bandwidth, FPS, and server-reported frame timings.",
        }
    write_json(output_path, recommendation)


def config_to_server_flags(config: dict[str, Any]) -> str:
    optimize_flag = "--jpeg-optimize" if config.get("jpeg_optimize") else "--no-jpeg-optimize"
    return " ".join(
        [
            "--fps",
            str(config["fps"]),
            "--quality",
            str(config["quality"]),
            "--scale",
            str(config["scale"]),
            "--jpeg-backend",
            str(config["jpeg_backend"]),
            "--delta-mode",
            str(config["delta_mode"]),
            "--tile-size",
            str(config["tile_size"]),
            "--full-frame-interval",
            str(config["full_frame_interval"]),
            optimize_flag,
        ]
    )


def run_config_sweep(output_path: str, result_dir: str, seconds: float) -> None:
    result_root = Path(result_dir)
    result_root.mkdir(parents=True, exist_ok=True)
    passcode = "123456"
    base_port = 51600
    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidate_configs()):
        port = base_port + index
        name = candidate["name"]
        server_profile = result_root / f"{name}-server.json"
        client_profile = result_root / f"{name}-client.json"
        server_out = result_root / f"{name}-server.out.log"
        server_err = result_root / f"{name}-server.err.log"
        for path in (server_profile, client_profile, server_out, server_err):
            if path.exists():
                path.unlink()
        server_args = [
            sys.executable,
            "-m",
            "home_remote_desktop.server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--passcode",
            passcode,
            "--profile-output",
            str(server_profile),
            *candidate["server_args"],
        ]
        with open(server_out, "w", encoding="utf-8") as out, open(server_err, "w", encoding="utf-8") as err:
            process = subprocess.Popen(server_args, stdout=out, stderr=err)
        try:
            wait_for_port("127.0.0.1", port, timeout=8.0)
            run_profile("127.0.0.1", port, passcode, seconds, str(client_profile))
            time.sleep(1.0)
        except Exception as exc:
            results.append({"name": name, "ok": False, "error": str(exc), "server_args": candidate["server_args"]})
            continue
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)

        if not server_profile.exists() or not client_profile.exists():
            results.append({"name": name, "ok": False, "error": "profile output was not created", "server_args": candidate["server_args"]})
            continue
        server_data = json.loads(server_profile.read_text(encoding="utf-8"))
        client_data = json.loads(client_profile.read_text(encoding="utf-8"))
        score = config_score(server_data, client_data)
        results.append(
            {
                "name": name,
                "ok": True,
                "score": score,
                "server_args": candidate["server_args"],
                "server_profile": str(server_profile),
                "client_profile": str(client_profile),
                "fps": client_data["fps"],
                "mbps": client_data["mbps"],
                "decode_ms_avg": client_data["decode_ms"]["avg"],
                "server_frame_ms_avg": server_data["frame_total_ms"]["avg"],
                "server_stream": server_data["stream"],
            }
        )

    successful = [result for result in results if result.get("ok")]
    best = max(successful, key=lambda item: item["score"]) if successful else None
    recommendation = {
        "role": "configuration-recommendation",
        "results": results,
        "recommended": None,
    }
    if best:
        server_flags = " ".join(best["server_args"])
        recommendation["recommended"] = {
            "name": best["name"],
            "score": best["score"],
            "server_command": f".\\run-server.bat --passcode 123456 {server_flags}",
            "client_command": ".\\run-client.bat",
            "profile_command": ".\\run-client.bat --host 127.0.0.1 --passcode 123456 --profile-seconds 10 --profile-output client-profile.json",
            "why": "Chosen by a local score that favors higher FPS and lower bandwidth/decode cost.",
        }
    write_json(output_path, recommendation)


def wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"server did not listen on {host}:{port}: {last_error}")


def config_score(server_data: dict[str, Any], client_data: dict[str, Any]) -> float:
    fps = float(client_data.get("fps", 0.0))
    mbps = float(client_data.get("mbps", 0.0))
    decode_ms = float(client_data.get("decode_ms", {}).get("avg", 0.0))
    frame_ms = float(server_data.get("frame_total_ms", {}).get("avg", 0.0))
    return fps - (mbps * 0.03) - (decode_ms * 0.01) - (frame_ms * 0.002)


def pair_config_score(client_data: dict[str, Any], server_data: dict[str, Any]) -> float:
    fps = float(client_data.get("fps", 0.0))
    mbps = float(client_data.get("mbps", 0.0))
    decode_ms = float(client_data.get("decode_ms", {}).get("avg", 0.0))
    server_frame_ms = float(server_data.get("frame_ms", {}).get("avg", 0.0))
    end_to_end_ms = float(client_data.get("end_to_end_ms", {}).get("avg", 0.0))
    return fps - (mbps * 0.04) - (decode_ms * 0.01) - (server_frame_ms * 0.002) - (end_to_end_ms * 0.001)


def main() -> None:
    args = parse_args()
    if args.pair_profile_sweep:
        if not args.host or not args.passcode:
            raise SystemExit("--pair-profile-sweep requires --host and --passcode")
        run_pair_profile_sweep(args.host, args.port, args.passcode, args.pair_profile_seconds, args.pair_profile_output)
        return
    if args.profile_config_sweep:
        run_config_sweep(args.profile_config_output, args.profile_config_dir, args.profile_config_seconds)
        return
    if args.profile_seconds > 0:
        if not args.host or not args.passcode:
            raise SystemExit("--profile-seconds requires --host and --passcode")
        run_profile(args.host, args.port, args.passcode, args.profile_seconds, args.profile_output)
        return
    app = RemoteDesktopClient(args.host, args.port, args.passcode, input_debug=args.input_debug)
    app.mainloop()


if __name__ == "__main__":
    main()
