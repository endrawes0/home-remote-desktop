from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageChops

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
    "Super_L": "win",
    "Super_R": "win",
    "Meta_L": "win",
    "Meta_R": "win",
    "Win_L": "win",
    "Win_R": "win",
}


@dataclass
class CaptureState:
    left: int
    top: int
    width: int
    height: int


@dataclass
class EncodedFrame:
    mode: str
    payload: bytes
    tiles: list[dict[str, int]]
    changed_tiles: int
    total_tiles: int
    image_width: int
    image_height: int


@dataclass
class StreamConfig:
    name: str
    fps: int
    quality: int
    scale: float
    jpeg_backend: str
    jpeg_optimize: bool
    turbojpeg_lib_path: str | None
    delta_mode: str
    tile_size: int
    full_frame_interval: int
    generation: int = 0


class StreamConfigState:
    def __init__(self, config: StreamConfig) -> None:
        self._config = config
        self._lock = threading.Lock()

    def get(self) -> StreamConfig:
        with self._lock:
            return StreamConfig(**vars(self._config))

    def update(self, config: StreamConfig) -> None:
        with self._lock:
            config.generation = self._config.generation + 1
            self._config = config


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
    changed_tiles: list[float] = field(default_factory=list)
    total_tiles: list[float] = field(default_factory=list)
    image_width: int = 0
    image_height: int = 0
    screen_width: int = 0
    screen_height: int = 0
    quality: int = 0
    scale: float = 0.0
    fps_limit: int = 0
    capture_backend: str = ""
    jpeg_backend: str = ""
    turbojpeg_lib_path: str = ""
    jpeg_optimize: bool = False
    delta_mode: str = ""
    tile_size: int = 0
    full_frame_interval: int = 0

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
        capture_backend: str,
        jpeg_backend: str,
        jpeg_optimize: bool,
        turbojpeg_lib_path: str | None,
        delta_mode: str,
        tile_size: int,
        full_frame_interval: int,
        changed_tiles: int,
        total_tiles: int,
    ) -> None:
        self.frame_count += 1
        self.bytes_sent += payload_size
        self.capture_ms.append(capture_ms)
        self.convert_resize_ms.append(convert_resize_ms)
        self.encode_ms.append(encode_ms)
        self.send_ms.append(send_ms)
        self.frame_total_ms.append(frame_total_ms)
        self.payload_bytes.append(float(payload_size))
        self.changed_tiles.append(float(changed_tiles))
        self.total_tiles.append(float(total_tiles))
        self.image_width = image_width
        self.image_height = image_height
        self.screen_width = state.width
        self.screen_height = state.height
        self.quality = quality
        self.scale = scale
        self.fps_limit = fps_limit
        self.capture_backend = capture_backend
        self.jpeg_backend = jpeg_backend
        self.turbojpeg_lib_path = turbojpeg_lib_path or ""
        self.jpeg_optimize = jpeg_optimize
        self.delta_mode = delta_mode
        self.tile_size = tile_size
        self.full_frame_interval = full_frame_interval

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
                    "capture_backend": self.capture_backend,
                    "jpeg_backend": self.jpeg_backend,
                    "turbojpeg_lib_path": self.turbojpeg_lib_path,
                    "jpeg_optimize": self.jpeg_optimize,
                    "delta_mode": self.delta_mode,
                    "tile_size": self.tile_size,
                    "full_frame_interval": self.full_frame_interval,
                },
                "changed_tiles": summarize(self.changed_tiles),
                "total_tiles": summarize(self.total_tiles),
                "payload_bytes": summarize(self.payload_bytes),
                "capture_ms": summarize(self.capture_ms),
                "convert_resize_ms": summarize(self.convert_resize_ms),
                "encode_ms": summarize(self.encode_ms),
                "send_ms": summarize(self.send_ms),
                "frame_total_ms": summarize(self.frame_total_ms),
            },
        )


class CaptureBackend:
    name = "base"

    def get_state(self) -> CaptureState:
        raise NotImplementedError

    def grab(self) -> Image.Image:
        raise NotImplementedError

    def close(self) -> None:
        pass


class MssCapture(CaptureBackend):
    name = "mss"

    def __init__(self, monitor_index: int = 1) -> None:
        import mss

        self.screen = mss.MSS()
        if monitor_index < 0 or monitor_index >= len(self.screen.monitors):
            raise ValueError(f"invalid MSS monitor index {monitor_index}; available 0-{len(self.screen.monitors) - 1}")
        monitor = self.screen.monitors[monitor_index]
        self.monitor = {"left": monitor["left"], "top": monitor["top"], "width": monitor["width"], "height": monitor["height"]}
        self.state = CaptureState(monitor["left"], monitor["top"], monitor["width"], monitor["height"])

    def get_state(self) -> CaptureState:
        return self.state

    def grab(self) -> Image.Image:
        shot = self.screen.grab(self.monitor)
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def close(self) -> None:
        self.screen.close()


class DxcamCapture(CaptureBackend):
    name = "dxcam"

    def __init__(self) -> None:
        import dxcam

        self.camera = dxcam.create(output_idx=0, output_color="RGB")
        frame = self.camera.grab()
        if frame is None:
            raise RuntimeError("dxcam did not return a frame")
        self.last_frame = frame
        height, width = frame.shape[:2]
        self.state = CaptureState(0, 0, width, height)

    def get_state(self) -> CaptureState:
        return self.state

    def grab(self) -> Image.Image:
        frame = self.camera.grab()
        if frame is None:
            frame = self.last_frame
        else:
            self.last_frame = frame
        return Image.fromarray(frame, "RGB")

    def close(self) -> None:
        try:
            self.camera.stop()
        except Exception:
            pass
        try:
            self.camera.release()
        except Exception:
            pass


class JpegEncoder:
    name = "pillow"

    def __init__(self, quality: int, optimize: bool) -> None:
        self.quality = quality
        self.optimize = optimize

    def encode(self, image: Image.Image) -> bytes:
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=self.quality, optimize=self.optimize)
        return output.getvalue()


class TurboJpegEncoder(JpegEncoder):
    name = "turbojpeg"

    def __init__(self, quality: int, optimize: bool, lib_path: str | None) -> None:
        super().__init__(quality, optimize)
        try:
            from turbojpeg import TurboJPEG
        except ImportError as exc:
            raise RuntimeError("turbojpeg package is not installed") from exc
        resolved_lib_path = lib_path or find_turbojpeg_library()
        if resolved_lib_path and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(os.path.dirname(resolved_lib_path))
        self.jpeg = TurboJPEG(resolved_lib_path) if resolved_lib_path else TurboJPEG()

    def encode(self, image: Image.Image) -> bytes:
        # PyTurboJPEG accepts numpy arrays; import lazily so Pillow remains the default dependency.
        import numpy

        return self.jpeg.encode(numpy.asarray(image), quality=self.quality)


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
        capture_backend: str,
        jpeg_backend: str,
        jpeg_optimize: bool,
        turbojpeg_lib_path: str | None,
        delta_mode: str,
        tile_size: int,
        full_frame_interval: int,
        mss_monitor_index: int,
        trace_input: bool,
    ):
        self.name = name
        self.passcode = passcode
        self.host = host
        self.port = port
        self.fps = max(1, min(fps, 30))
        self.quality = max(35, min(quality, 95))
        self.scale = max(0.2, min(scale, 1.0))
        self.profile_output = profile_output
        self.capture_backend_name = capture_backend
        self.jpeg_backend_name = jpeg_backend
        self.jpeg_optimize = jpeg_optimize
        self.turbojpeg_lib_path = turbojpeg_lib_path
        self.delta_mode = delta_mode
        self.tile_size = max(64, min(tile_size, 1024))
        self.full_frame_interval = max(1, full_frame_interval)
        self.mss_monitor_index = max(0, mss_monitor_index)
        self.trace_input = trace_input
        self.stop_event = threading.Event()

    def default_stream_config(self) -> StreamConfig:
        return StreamConfig(
            name="default",
            fps=self.fps,
            quality=self.quality,
            scale=self.scale,
            jpeg_backend=self.jpeg_backend_name,
            jpeg_optimize=self.jpeg_optimize,
            turbojpeg_lib_path=self.turbojpeg_lib_path,
            delta_mode=self.delta_mode,
            tile_size=self.tile_size,
            full_frame_interval=self.full_frame_interval,
        )

    def stream_config_from_message(self, message: dict[str, Any]) -> StreamConfig:
        return StreamConfig(
            name=str(message.get("name") or "remote-config"),
            fps=max(1, min(int(message.get("fps", self.fps)), 30)),
            quality=max(35, min(int(message.get("quality", self.quality)), 95)),
            scale=max(0.2, min(float(message.get("scale", self.scale)), 1.0)),
            jpeg_backend=str(message.get("jpeg_backend") or self.jpeg_backend_name),
            jpeg_optimize=bool(message.get("jpeg_optimize", self.jpeg_optimize)),
            turbojpeg_lib_path=message.get("turbojpeg_lib_path") or self.turbojpeg_lib_path,
            delta_mode=str(message.get("delta_mode") or self.delta_mode),
            tile_size=max(64, min(int(message.get("tile_size", self.tile_size)), 1024)),
            full_frame_interval=max(1, int(message.get("full_frame_interval", self.full_frame_interval))),
        )

    def start(self) -> None:
        discovery = threading.Thread(target=self._discovery_loop, daemon=True)
        discovery.start()
        try:
            self._tcp_loop()
        except KeyboardInterrupt:
            print("\nStopping server.")
            self.stop_event.set()

    def _discovery_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.5)
            sock.bind(("", DISCOVERY_PORT))
            while not self.stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
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
        finally:
            sock.close()

    def _tcp_loop(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.settimeout(0.5)
            server.bind((self.host, self.port))
            server.listen(5)
            print(f"Server: {self.name}")
            print(f"Address: {socket_ipv4()}:{self.port}")
            print(f"Discovery UDP port: {DISCOVERY_PORT}")
            print(f"Passcode: {self.passcode}")
            print("Waiting for a client. Press Ctrl+C to stop.")
            while not self.stop_event.is_set():
                try:
                    client, addr = server.accept()
                except socket.timeout:
                    continue
                print(f"Client connected from {addr[0]}:{addr[1]}")
                threading.Thread(target=self._handle_client, args=(client, addr), daemon=True).start()
        finally:
            server.close()

    def _handle_client(self, client: socket.socket, addr: tuple[str, int]) -> None:
        alive: threading.Event | None = None
        stream: threading.Thread | None = None
        focus: threading.Thread | None = None
        profile: ServerProfile | None = None
        try:
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            hello, _ = recv_packet(client)
            if hello.get("type") != "hello" or hello.get("passcode") != self.passcode:
                send_packet(client, {"type": "auth", "ok": False, "error": "bad passcode"})
                return

            mss, pyautogui = import_capture_modules()
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0
            if hasattr(pyautogui, "MINIMUM_DURATION"):
                pyautogui.MINIMUM_DURATION = 0
            if hasattr(pyautogui, "MINIMUM_SLEEP"):
                pyautogui.MINIMUM_SLEEP = 0
            capture = create_capture_backend(self.capture_backend_name, self.mss_monitor_index)
            state = capture.get_state()
            capture.close()

            send_packet(client, {"type": "auth", "ok": True, "screen_w": state.width, "screen_h": state.height})
            send_lock = threading.Lock()
            alive = threading.Event()
            alive.set()
            config_state = StreamConfigState(self.default_stream_config())
            if self.profile_output:
                profile = ServerProfile(self.profile_output)
            stream = threading.Thread(
                target=self._stream_frames,
                args=(client, send_lock, alive, state, profile, config_state),
                daemon=True,
            )
            stream.start()
            focus = threading.Thread(
                target=self._keyboard_focus_loop,
                args=(client, send_lock, alive),
                daemon=True,
            )
            focus.start()
            self._input_loop(client, pyautogui, alive, state, config_state)
        except (ConnectionError, OSError):
            print(f"Client {addr[0]} disconnected", flush=True)
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
            if focus:
                focus.join(timeout=1.0)
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
        config_state: StreamConfigState,
    ) -> None:
        interval = 1.0 / self.fps
        frame = 0
        capture = create_capture_backend(self.capture_backend_name, self.mss_monitor_index)
        config = config_state.get()
        encoder = create_jpeg_encoder(config.jpeg_backend, config.quality, config.jpeg_optimize, config.turbojpeg_lib_path)
        previous: Image.Image | None = None
        current_generation = config.generation
        try:
            while alive.is_set():
                config = config_state.get()
                if config.generation != current_generation:
                    encoder = create_jpeg_encoder(config.jpeg_backend, config.quality, config.jpeg_optimize, config.turbojpeg_lib_path)
                    previous = None
                    current_generation = config.generation
                interval = 1.0 / config.fps
                started = time.perf_counter()
                capture_started = time.perf_counter()
                image = capture.grab()
                captured = time.perf_counter()
                if config.scale != 1.0:
                    new_size = (max(1, int(image.width * config.scale)), max(1, int(image.height * config.scale)))
                    image = image.resize(new_size, Image.Resampling.BILINEAR)
                converted = time.perf_counter()
                encoded_frame = encode_stream_frame(
                    image=image,
                    previous=previous,
                    encoder=encoder,
                    frame=frame,
                    delta_mode=config.delta_mode,
                    tile_size=config.tile_size,
                    full_frame_interval=config.full_frame_interval,
                )
                encoded = time.perf_counter()
                previous = image.copy()
                packet = {
                    "type": "frame",
                    "frame": frame,
                    "server_wall_ns": time.time_ns(),
                    "screen_w": state.width,
                    "screen_h": state.height,
                    "image_w": encoded_frame.image_width,
                    "image_h": encoded_frame.image_height,
                    "format": "jpeg",
                    "mode": encoded_frame.mode,
                    "tiles": encoded_frame.tiles,
                    "config_name": config.name,
                    "config_generation": config.generation,
                    "capture_ms": (captured - capture_started) * 1000.0,
                    "convert_resize_ms": (converted - captured) * 1000.0,
                    "encode_ms": (encoded - converted) * 1000.0,
                    "server_frame_ms": (encoded - started) * 1000.0,
                    "changed_tiles": encoded_frame.changed_tiles,
                    "total_tiles": encoded_frame.total_tiles,
                    "capture_backend": capture.name,
                    "jpeg_backend": encoder.name,
                }
                try:
                    send_started = time.perf_counter()
                    with send_lock:
                        send_packet(client, packet, encoded_frame.payload)
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
                        payload_size=len(encoded_frame.payload),
                        image_width=encoded_frame.image_width,
                        image_height=encoded_frame.image_height,
                        state=state,
                        quality=config.quality,
                        scale=config.scale,
                        fps_limit=config.fps,
                        capture_backend=capture.name,
                        jpeg_backend=encoder.name,
                        jpeg_optimize=config.jpeg_optimize,
                        turbojpeg_lib_path=config.turbojpeg_lib_path,
                        delta_mode=config.delta_mode,
                        tile_size=config.tile_size,
                        full_frame_interval=config.full_frame_interval,
                        changed_tiles=encoded_frame.changed_tiles,
                        total_tiles=encoded_frame.total_tiles,
                    )
                frame += 1
                elapsed = time.perf_counter() - started
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        finally:
            capture.close()

    def _input_loop(
        self,
        client: socket.socket,
        pyautogui: Any,
        alive: threading.Event,
        state: CaptureState,
        config_state: StreamConfigState,
    ) -> None:
        while alive.is_set():
            message, _ = recv_packet(client)
            if message.get("type") != "input":
                continue
            event = message.get("event")
            if event == "set_stream_config":
                config_state.update(self.stream_config_from_message(message))
                continue
            if event == "move_relative":
                dx = float(message.get("dx", 0))
                dy = float(message.get("dy", 0))
                if self.trace_input:
                    print(f"Input: move_relative dx={dx:.1f} dy={dy:.1f}", flush=True)
                pyautogui.moveRel(dx, dy, duration=0)
                continue
            if event == "click_current":
                button = message.get("button", "left")
                if self.trace_input:
                    print(f"Input: click_current {button}", flush=True)
                pyautogui.click(button=button)
                continue
            if event in {"move", "down", "up", "click"}:
                nx = min(1.0, max(0.0, float(message.get("nx", 0))))
                ny = min(1.0, max(0.0, float(message.get("ny", 0))))
                x = state.left + min(state.width - 1, int(nx * state.width))
                y = state.top + min(state.height - 1, int(ny * state.height))
                button = message.get("button", "left")
                if self.trace_input and event != "move":
                    print(f"Input: {event} {button} nx={nx:.3f} ny={ny:.3f} x={x} y={y}", flush=True)
                if event == "move":
                    pyautogui.moveTo(x, y, duration=0)
                elif event == "down":
                    pyautogui.mouseDown(x, y, button=button)
                elif event == "up":
                    pyautogui.mouseUp(x, y, button=button)
                elif event == "click":
                    pyautogui.click(x, y, button=button)
            elif event == "wheel":
                pyautogui.scroll(int(message.get("delta", 0)))
            elif event == "text":
                text = str(message.get("text", ""))
                if text:
                    if self.trace_input:
                        print(f"Input: text {len(text)} chars", flush=True)
                    pyautogui.write(text, interval=0)
            elif event == "press":
                key = normalize_key(message)
                if key:
                    if self.trace_input:
                        print(f"Input: press {key}", flush=True)
                    pyautogui.press(key)
            elif event in {"key_down", "key_up"}:
                key = normalize_key(message)
                if key:
                    if self.trace_input:
                        print(f"Input: {event} {key}", flush=True)
                    if event == "key_down":
                        pyautogui.keyDown(key)
                    else:
                        pyautogui.keyUp(key)

    def _keyboard_focus_loop(
        self,
        client: socket.socket,
        send_lock: threading.Lock,
        alive: threading.Event,
    ) -> None:
        detector = WindowsTextFocusDetector()
        last_editable: bool | None = None
        while alive.is_set():
            editable = detector.is_text_entry_focused()
            if editable != last_editable:
                packet = {
                    "type": "ime",
                    "action": "show" if editable else "hide",
                    "text_entry_focused": editable,
                }
                try:
                    with send_lock:
                        send_packet(client, packet)
                except OSError:
                    alive.clear()
                    return
                last_editable = editable
            time.sleep(0.25)


class WindowsTextFocusDetector:
    EDITABLE_CLASS_PARTS = (
        "edit",
        "richedit",
        "text",
        "textbox",
        "scintilla",
        "consolewindowclass",
    )

    def __init__(self) -> None:
        self.uia: Any | None = None
        self.uia_defs: Any | None = None
        try:
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
            from comtypes.gen import UIAutomationClient as uia_defs

            self.uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
            self.uia_defs = uia_defs
        except Exception:
            self.uia = None
            self.uia_defs = None

    def is_text_entry_focused(self) -> bool:
        uia_result = self._uia_text_entry_focused()
        if uia_result is not None:
            return uia_result
        return self._focused_class_is_editable()

    def _uia_text_entry_focused(self) -> bool | None:
        if self.uia is None or self.uia_defs is None:
            return None
        try:
            element = self.uia.GetFocusedElement()
            if not element or not bool(element.CurrentIsEnabled):
                return False
            control_type = int(element.CurrentControlType)
            class_name = str(element.CurrentClassName or "").lower()
            defs = self.uia_defs
            if control_type in {
                defs.UIA_EditControlTypeId,
                defs.UIA_DocumentControlTypeId,
                defs.UIA_ComboBoxControlTypeId,
            }:
                return True
            value_available = bool(element.GetCurrentPropertyValue(defs.UIA_IsValuePatternAvailablePropertyId))
            text_available = bool(element.GetCurrentPropertyValue(defs.UIA_IsTextPatternAvailablePropertyId))
            if value_available and any(part in class_name for part in self.EDITABLE_CLASS_PARTS):
                return True
            if text_available and any(part in class_name for part in self.EDITABLE_CLASS_PARTS):
                return True
            return False
        except Exception:
            return None

    def _focused_class_is_editable(self) -> bool:
        hwnd = focused_control_hwnd()
        if not hwnd:
            return False
        class_name = window_class_name(hwnd).lower()
        return any(part in class_name for part in self.EDITABLE_CLASS_PARTS)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("hwndActive", ctypes.c_void_p),
        ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p),
        ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", ctypes.c_long * 4),
    ]


def focused_control_hwnd() -> int:
    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return 0
    thread_id = user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground), None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return int(info.hwndFocus or foreground)
    return int(foreground)


def window_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(ctypes.c_void_p(hwnd), buffer, len(buffer))
    return buffer.value


def create_capture_backend(name: str, mss_monitor_index: int = 1) -> CaptureBackend:
    if name == "mss":
        return MssCapture(mss_monitor_index)
    if name == "dxcam":
        return DxcamCapture()
    if name == "auto":
        try:
            return DxcamCapture()
        except Exception:
            return MssCapture(mss_monitor_index)
    raise ValueError(f"unknown capture backend: {name}")


def create_jpeg_encoder(name: str, quality: int, optimize: bool, turbojpeg_lib_path: str | None) -> JpegEncoder:
    if name == "pillow":
        return JpegEncoder(quality, optimize)
    if name == "turbojpeg":
        return TurboJpegEncoder(quality, optimize, turbojpeg_lib_path)
    if name == "auto":
        try:
            return TurboJpegEncoder(quality, optimize, turbojpeg_lib_path)
        except Exception:
            return JpegEncoder(quality, optimize)
    raise ValueError(f"unknown JPEG backend: {name}")


def find_turbojpeg_library() -> str | None:
    candidates: list[str] = []
    for env_name in ("TURBOJPEG_LIB_PATH", "TURBOJPEG"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            candidates.append(os.path.join(directory, "turbojpeg.dll"))
    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles(x86)")]
    for root in program_files:
        if not root:
            continue
        candidates.extend(
            [
                os.path.join(root, "libjpeg-turbo64", "bin", "turbojpeg.dll"),
                os.path.join(root, "libjpeg-turbo", "bin", "turbojpeg.dll"),
            ]
        )
    candidates.extend(
        [
            r"C:\libjpeg-turbo64\bin\turbojpeg.dll",
            r"C:\libjpeg-turbo\bin\turbojpeg.dll",
        ]
    )
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def encode_stream_frame(
    *,
    image: Image.Image,
    previous: Image.Image | None,
    encoder: JpegEncoder,
    frame: int,
    delta_mode: str,
    tile_size: int,
    full_frame_interval: int,
) -> EncodedFrame:
    if delta_mode == "off" or previous is None or frame % full_frame_interval == 0:
        return EncodedFrame(
            mode="full",
            payload=encoder.encode(image),
            tiles=[],
            changed_tiles=1,
            total_tiles=1,
            image_width=image.width,
            image_height=image.height,
        )
    if previous.size != image.size:
        return EncodedFrame(
            mode="full",
            payload=encoder.encode(image),
            tiles=[],
            changed_tiles=1,
            total_tiles=1,
            image_width=image.width,
            image_height=image.height,
        )
    return encode_delta_frame(image, previous, encoder, tile_size)


def encode_delta_frame(image: Image.Image, previous: Image.Image, encoder: JpegEncoder, tile_size: int) -> EncodedFrame:
    payload_parts: list[bytes] = []
    tiles: list[dict[str, int]] = []
    total_tiles = 0
    width, height = image.size
    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            total_tiles += 1
            box = (x, y, min(x + tile_size, width), min(y + tile_size, height))
            current_tile = image.crop(box)
            previous_tile = previous.crop(box)
            if not ImageChops.difference(current_tile, previous_tile).getbbox():
                continue
            encoded = encoder.encode(current_tile)
            payload_parts.append(encoded)
            tiles.append(
                {
                    "x": x,
                    "y": y,
                    "w": box[2] - box[0],
                    "h": box[3] - box[1],
                    "size": len(encoded),
                }
            )
    return EncodedFrame(
        mode="delta",
        payload=b"".join(payload_parts),
        tiles=tiles,
        changed_tiles=len(tiles),
        total_tiles=total_tiles,
        image_width=width,
        image_height=height,
    )


def normalize_key(message: dict[str, Any]) -> str | None:
    char = message.get("char") or ""
    keysym = message.get("keysym") or ""
    if len(char) == 1 and char.isprintable():
        return char
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
    parser.add_argument("--capture-backend", choices=["mss", "dxcam", "auto"], default="mss", help="Screen capture backend")
    parser.add_argument("--mss-monitor-index", type=int, default=1, help="MSS monitor index to stream; 0 captures the virtual desktop")
    parser.add_argument("--jpeg-backend", choices=["pillow", "turbojpeg", "auto"], default="pillow", help="JPEG encoder backend")
    parser.add_argument("--jpeg-optimize", action=argparse.BooleanOptionalAction, default=False, help="Enable Pillow JPEG optimize")
    parser.add_argument("--turbojpeg-lib-path", default=None, help="Path to the native turbojpeg DLL when using PyTurboJPEG")
    parser.add_argument("--delta-mode", choices=["off", "tiles"], default="off", help="Send changed JPEG tiles after full frames")
    parser.add_argument("--tile-size", type=int, default=384, help="Delta tile size in pixels")
    parser.add_argument("--full-frame-interval", type=int, default=90, help="Send a full frame every N frames in delta mode")
    parser.add_argument("--trace-input", action="store_true", help="Print input events received from clients")
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
        args.capture_backend,
        args.jpeg_backend,
        args.jpeg_optimize,
        args.turbojpeg_lib_path,
        args.delta_mode,
        args.tile_size,
        args.full_frame_interval,
        args.mss_monitor_index,
        args.trace_input,
    ).start()


if __name__ == "__main__":
    main()
