from __future__ import annotations

import argparse
import io
import json
import queue
import socket
import threading
import time
import tkinter as tk
from dataclasses import dataclass
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


class RemoteDesktopClient(tk.Tk):
    def __init__(self, host: str | None, port: int, passcode: str | None):
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
        self.image_size = (1, 1)
        self.screen_size = (1, 1)
        self.connected = False

        self._build_ui()
        self.after(50, self._drain_frames)
        if self.host:
            self.after(100, self._connect_prompted)
        else:
            self.after(100, self.discover)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 6))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        self.server_var = tk.StringVar()
        self.servers: list[ServerInfo] = []
        self.server_combo = ttk.Combobox(toolbar, textvariable=self.server_var, state="readonly", width=48)
        self.server_combo.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(toolbar, text="Discover", command=self.discover).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Connect", command=self._connect_selected).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Disconnect", command=self.disconnect).pack(side=tk.LEFT, padx=(0, 12))

        self.status_var = tk.StringVar(value="Discovering servers...")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.canvas = tk.Canvas(self, bg="#101010", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._redraw_current())
        self.canvas.bind("<Motion>", self._mouse_move)
        self.canvas.bind("<ButtonPress-1>", lambda e: self._mouse_button(e, "down", "left"))
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._mouse_button(e, "up", "left"))
        self.canvas.bind("<ButtonPress-2>", lambda e: self._mouse_button(e, "down", "middle"))
        self.canvas.bind("<ButtonRelease-2>", lambda e: self._mouse_button(e, "up", "middle"))
        self.canvas.bind("<ButtonPress-3>", lambda e: self._mouse_button(e, "down", "right"))
        self.canvas.bind("<ButtonRelease-3>", lambda e: self._mouse_button(e, "up", "right"))
        self.canvas.bind("<MouseWheel>", self._mouse_wheel)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.focus_set())
        self.canvas.bind("<KeyPress>", self._key_down)
        self.canvas.bind("<KeyRelease>", self._key_up)

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
            image = Image.open(io.BytesIO(payload))
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
        pass

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
        except OSError:
            self.disconnect()

    def _mouse_move(self, event: tk.Event[Any]) -> None:
        point = self._normalized_point(event)
        if point:
            self._send_input({"event": "move", "nx": point[0], "ny": point[1]})

    def _mouse_button(self, event: tk.Event[Any], action: str, button: str) -> None:
        self.canvas.focus_set()
        point = self._normalized_point(event)
        if point:
            self._send_input({"event": action, "button": button, "nx": point[0], "ny": point[1]})

    def _mouse_wheel(self, event: tk.Event[Any]) -> None:
        delta = 1 if event.delta > 0 else -1
        self._send_input({"event": "wheel", "delta": delta * 5})

    def _key_down(self, event: tk.Event[Any]) -> None:
        self._send_input({"event": "key_down", "keysym": event.keysym, "char": event.char})

    def _key_up(self, event: tk.Event[Any]) -> None:
        self._send_input({"event": "key_up", "keysym": event.keysym, "char": event.char})

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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = RemoteDesktopClient(args.host, args.port, args.passcode)
    app.mainloop()


if __name__ == "__main__":
    main()
