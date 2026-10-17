"""Temporary per-key animations and their shared USB streaming session."""

from collections import deque
import colorsys
import math
from pathlib import Path
import threading
import time

from aula import DeviceError, parse_color
from keyboard_device import connect_keyboard as Keyboard
from aula_supernova import Supernova
from keyboard_layouts import F87_LAYOUT
from keyboard_profiles import WIRED_RGB


LIVE_LABELS = {"aurora": "Aurora + key ripples", "supernova": "Supernova",
               "video": "Video projection", "screen": "Screen projection",
               "wireless-solid": "Solid color over dongle"}
LIVE_HINTS = {
    "aurora": "Type anywhere to send ripples through the aurora.",
    "supernova": "Type to feed gravity. Space explodes. Backspace reverses spin.",
    "video": "Choose a video. Its colors and motion play across the keys.",
    "screen": "Mirror a display's colors onto the keys while this window is open.",
    "wireless-solid": "Keep the panel open for wireless color. Closing it restores previous lighting.",
}


LAYOUT = F87_LAYOUT


class Aurora:
    def __init__(self, started=0, *, layout=None):
        self.layout = F87_LAYOUT if layout is None else layout
        self.started = started
        self.ripples = deque(maxlen=32)

    def press(self, name, now):
        position = self.layout.get(name)
        if position is not None:
            self.ripples.append((now, position[1], position[2]))

    def render(self, now, color, brightness, speed):
        rgb = parse_color(color)
        if not 0 <= brightness <= 4 or not 0 <= speed <= 4:
            raise ValueError("Brightness and speed must be between 0 and 4.")
        hue, _, _ = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
        elapsed = now - self.started
        phase = elapsed * (0.22 + speed * 0.16)
        while self.ripples and now - self.ripples[0][0] > 2.2:
            self.ripples.popleft()
        colors = {}
        for index, x, y in self.layout.values():
            curtain = math.sin(x * 0.36 + math.sin(y * 0.7 + phase) * 1.5 - phase)
            glow = 0.14 + 0.5 * ((curtain + 1) / 2) ** 2
            tint = (hue + 0.23 * math.sin(x * 0.18 + y * 0.26 + phase * 0.4)) % 1
            channels = list(colorsys.hsv_to_rgb(tint, 0.92, glow))
            sparkle = max(0, math.sin(elapsed * 1.7 + index * 2.399)) ** 36 * 0.26
            channels = [min(1, value + sparkle) for value in channels]
            for pressed, origin_x, origin_y in self.ripples:
                age = max(0, now - pressed)
                distance = math.hypot(x - origin_x, (y - origin_y) * 1.2)
                radius = age * (5 + speed * 0.8)
                ring = math.exp(-((distance - radius) / 0.7) ** 2) * math.exp(-age * 1.5)
                flash = math.exp(-distance * distance * 2 - age * 12)
                wave = colorsys.hsv_to_rgb((hue + age * 0.32 + 0.16) % 1, 0.65, 1)
                channels = [min(1, value + ring * wave[channel] + flash * 0.8)
                            for channel, value in enumerate(channels)]
            level = brightness / 4
            colors[index] = bytes(round(value * level * 255) for value in channels)
        return colors

    def frame(self, now, color, brightness, speed):
        return WIRED_RGB.encode(self.render(now, color, brightness, speed))


class Solid:
    def __init__(self, layout):
        self.layout = layout

    def press(self, *_):
        pass

    def render(self, now, color, brightness, speed):
        rgb = bytes(round(channel * brightness / 4) for channel in parse_color(color))
        return {position[0]: rgb for position in self.layout.values()}


class KeyInput:
    def __init__(self, *, layout=None, usb_parent=None, usb_id="258a:010c"):
        self.devices = []
        self.names = {}
        try:
            import evdev
        except ImportError:
            return
        self.ev_key = evdev.ecodes.EV_KEY
        vendor, product = usb_id.lower().split(":")
        layout = F87_LAYOUT if layout is None else layout
        self.names = {getattr(evdev.ecodes, "KEY_" + name): name for name in layout
                      if hasattr(evdev.ecodes, "KEY_" + name)}
        for entry in sorted(Path("/sys/class/input").glob("event*")):
            device = None
            try:
                if usb_parent is not None and Path(usb_parent) not in (entry / "device").resolve().parents:
                    continue
                identity = entry / "device/id"
                if ((identity / "vendor").read_text().strip() != vendor
                        or (identity / "product").read_text().strip() != product
                        or (identity / "bustype").read_text().strip() != "0003"):
                    continue
                device = evdev.InputDevice(str(Path("/dev/input") / entry.name))
                if evdev.ecodes.KEY_A in device.capabilities().get(self.ev_key, []):
                    self.devices.append(device)
                    device = None
            except OSError:
                pass
            finally:
                if device is not None:
                    device.close()

    def poll(self):
        presses = set()
        for device in self.devices:
            try:
                for event in device.read():
                    if event.type == self.ev_key and event.value == 1 and event.code in self.names:
                        presses.add(self.names[event.code])
            except BlockingIOError:
                pass
        return presses

    def close(self):
        for device in self.devices:
            device.close()


class LiveSession:
    def __init__(self, color, brightness, speed, *, effect="aurora", source=None, vividness=100,
                 device=None, model=None):
        if effect not in LIVE_LABELS:
            raise ValueError(f"Unknown live animation: {effect}")
        self.effect = effect
        self.selected_device = device
        self.selected_model = model
        self.profile = None
        self.transport = None
        self.source_name = source
        self.image_source = None
        if effect in {"video", "screen"}:
            from aula_projection import make_source
            self.image_source = make_source(effect, source)
        self.lock = threading.Lock()
        self.vividness = 100
        self.update(color, brightness, speed, vividness=vividness)
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.thread = None
        self.error = None
        self.backup = None
        self.device = None
        self.typing_reactions = False
        self.frames_sent = 0
        self.last_frame = None
        self.last_colors = None
        self.frame_times = deque(maxlen=120)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def update(self, color, brightness, speed, *, vividness=None):
        parse_color(color)
        if not 0 <= brightness <= 4 or not 0 <= speed <= 4:
            raise ValueError("Brightness and speed must be between 0 and 4.")
        if vividness is not None and not 0 <= vividness <= 300:
            raise ValueError("Vividness must be between 0 and 300 percent.")
        with self.lock:
            self.settings = (color, brightness, speed)
            if vividness is not None:
                self.vividness = vividness

    def state(self):
        if self.error:
            raise DeviceError(self.error)
        with self.lock:
            color, brightness, speed = self.settings
            vividness = self.vividness
            cutoff = time.monotonic() - 1
            usb_fps = sum(stamp > cutoff for stamp in self.frame_times)
        source_label = ("Display selected in system picker" if self.effect == "screen"
                        and self.source_name is None else str(self.source_name or "All displays"))
        return {"effect": self.effect, "color": "#" + parse_color(color).hex(),
                "brightness": brightness, "speed": speed, "streaming": self.running,
                "vividness": vividness,
                "backup": str(self.backup), "device": str(self.device),
                "model": self.profile.name if self.profile else None,
                "profile": self.profile.key if self.profile else None,
                "transport": self.transport,
                "writable": bool(self.profile and self.profile.writable),
                "streaming_supported": bool(self.profile and self.profile.streaming_supported),
                "brightness_max": 4,
                "onboard_brightness_max": self.profile.brightness_max if self.profile else 4,
                "target_fps": self.profile.fps if self.profile else None,
                "supported_effects": list(self.profile.effects) if self.profile else [],
                "source": source_label if self.image_source else None,
                "capture_fps": self.image_source.fps() if self.image_source else None,
                "usb_fps": usb_fps,
                "typing_reactions": self.typing_reactions, "frames_sent": self.frames_sent}

    def preview(self):
        with self.lock:
            return self.last_frame

    def preview_colors(self):
        with self.lock:
            return self.last_colors

    def start(self):
        self.thread = threading.Thread(target=self._run, name=f"aula-{self.effect}", daemon=True)
        self.thread.start()
        timeout = self.image_source.startup_timeout + 30 if self.image_source else 30
        if not self.ready.wait(timeout):
            self.stop()
            raise DeviceError(f"{LIVE_LABELS[self.effect]} startup timed out.")
        return self.state()

    def request_stop(self):
        self.stopped.set()
        if self.image_source:
            self.image_source.request_stop()

    def stop(self):
        self.request_stop()
        if self.thread:
            self.thread.join(10)
            if self.thread.is_alive():
                raise DeviceError("Waiting for the keyboard to finish its USB operation.")
        if self.error:
            raise DeviceError(self.error)

    def _run(self):
        inputs = None
        try:
            if self.image_source:
                from aula_projection import Projection
                try:
                    self.image_source.start()
                except InterruptedError:
                    if self.stopped.is_set():
                        return
                    raise
            if self.stopped.is_set():
                return
            with Keyboard(self.selected_device, model=self.selected_model) as keyboard:
                keyboard.require_writable()
                self.profile = keyboard.profile
                if not self.profile.streaming_supported:
                    raise DeviceError("Live streaming is not enabled for this keyboard revision.")
                self.transport = keyboard.identification()['transport']
                layout = self.profile.layout
                if self.image_source:
                    animation = Projection(layout, self.image_source)
                elif self.effect == 'wireless-solid':
                    if self.transport != 'wireless-2.4g':
                        raise ValueError('Use set --effect solid for a wired keyboard.')
                    animation = Solid(layout)
                elif self.effect == "aurora":
                    animation = Aurora(time.monotonic(), layout=layout)
                else:
                    animation = Supernova(layout, time.monotonic())
                config, palette = keyboard.read_config(), keyboard.read_palette()
                self.backup = keyboard.backup(config, palette)
                self.device = keyboard.path
                try:
                    keyboard.start_stream(config)
                    if not self.image_source and self.effect != 'wireless-solid':
                        inputs = KeyInput(layout=layout, usb_parent=keyboard.usb_parent,
                                          usb_id=keyboard.identification()['usb_id'])
                        self.typing_reactions = bool(inputs.devices)
                    while not self.stopped.is_set():
                        started = time.monotonic()
                        for name in sorted(inputs.poll() if inputs else ()):
                            animation.press(name, started)
                        with self.lock:
                            settings = self.settings
                            vividness = self.vividness
                        colors = (animation.render(started, *settings, vividness=vividness)
                                  if self.image_source else animation.render(started, *settings))
                        frame = keyboard.encode_colors(colors)
                        sent = keyboard.send_direct(frame)
                        with self.lock:
                            self.last_frame = bytes(frame)
                            self.last_colors = getattr(keyboard, 'rendered_colors', None) or colors
                            if sent is not False:
                                self.frame_times.append(time.monotonic())
                        if sent is not False:
                            self.frames_sent += 1
                        self.ready.set()
                        self.stopped.wait(max(0, 1 / self.profile.fps - (time.monotonic() - started)))
                finally:
                    keyboard.restore_stream(config, palette)
        except Exception as error:
            backup_note = (f"Original settings backup: {self.backup}." if self.backup is not None
                           else "No lighting settings were changed.")
            self.error = f"{LIVE_LABELS[self.effect]} stopped: {error}. {backup_note}"
        finally:
            if inputs:
                inputs.close()
            if self.image_source:
                try:
                    self.image_source.close()
                except Exception as error:
                    self.error = f"{self.error or ''} Image source cleanup failed: {error}".strip()
            self.ready.set()


AuroraSession = LiveSession
