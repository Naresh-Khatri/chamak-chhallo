"""Checked 20-byte output reports for the 3554:fa09 wireless receiver."""

import colorsys
import fcntl
import os
from pathlib import Path
import select
import time

from aula import DeviceError


REPORT_ID = 0x13
REPORT_SIZE = 20
CONFIG_COUNT = 10
REPORT_GAP = 0.013


def report(command, count, sequence, payload=b""):
    if len(payload) > 15:
        raise ValueError("Wireless report payload exceeds 15 bytes.")
    frame = bytearray(REPORT_SIZE)
    frame[:4] = bytes((REPORT_ID, command, count, sequence))
    frame[4:4 + len(payload)] = payload
    frame[-1] = sum(frame[:-1]) & 255
    return bytes(frame)


def validate_report(frame):
    if len(frame) != REPORT_SIZE or frame[0] != REPORT_ID:
        raise DeviceError("Malformed wireless report.")
    if frame[-1] != sum(frame[:-1]) & 255:
        raise DeviceError("Wireless report checksum mismatch.")


def config_payload(frames):
    if len(frames) != CONFIG_COUNT:
        raise DeviceError("Wireless config requires all ten fragments.")
    payload = bytearray()
    for sequence, frame in enumerate(frames):
        validate_report(frame)
        length = 14 if sequence < 9 else 2
        if frame[1:4] != bytes((0x44, CONFIG_COUNT, sequence)) or frame[4] != length:
            raise DeviceError("Wireless config fragment header or sequence mismatch.")
        # receiver leaves stale bytes beyond declared length
        payload.extend(frame[5:5 + length])
    if len(payload) != 128 or payload[-2:] != b"\x5a\xa5":
        raise DeviceError("Wireless config is missing its end marker.")
    return bytes(payload)


def config_equivalent(left, right):
    left, right = bytearray(config_payload(left)), bytearray(config_payload(right))
    # firmware owns confirm/apply flags
    for offset in (3, 9):
        left[offset] = right[offset] = 0
    return left == right


def color_payload(colors):
    groups = {}
    for index, rgb in sorted(colors.items()):
        if not isinstance(index, int) or not 0 <= index < 126:
            raise ValueError("Wireless LED index must be between 0 and 125.")
        rgb = bytes(rgb)
        if len(rgb) != 3:
            raise ValueError("Each wireless LED requires three RGB bytes.")
        groups.setdefault(rgb, []).append(index)
    payload = bytearray()
    for rgb, indices in groups.items():
        payload.extend(rgb)
        payload.append(len(indices))
        payload.extend(indices)
    return bytes(payload)


def encode_rgb(colors):
    """Send complete colour groups, coarsening HSV only when a frame cannot fit."""
    colors = {index: bytes(rgb) for index, rgb in colors.items()}
    payload = color_payload(colors)
    if not payload:
        return b"", {}
    if len(payload) > 196:
        hsv = {index: colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
               for index, rgb in colors.items()}
        peak = max(value for _, _, value in hsv.values())
        for hues, saturations, values in ((12, 2, 4), (8, 2, 3), (6, 1, 2), (4, 1, 1)):
            quantized = {}
            for index, (hue, saturation, value) in hsv.items():
                if value == 0:
                    quantized[index] = bytes(3)
                    continue
                hue = round(hue * hues) % hues / hues
                saturation = round(saturation * saturations) / saturations
                value = max(1, round(value / peak * values)) / values * peak
                quantized[index] = bytes(round(channel * 255) for channel in
                                         colorsys.hsv_to_rgb(hue, saturation, value))
            payload = color_payload(quantized)
            if len(payload) <= 196:
                colors = quantized
                break
        else:
            raise ValueError("A complete wireless RGB frame exceeds the packet budget.")
    count = (len(payload) + 13) // 14
    frames = [report(0x88, count, seq, bytes((0x10 + len(chunk),)) + chunk)
              for seq, start in enumerate(range(0, len(payload), 14))
              for chunk in (payload[start:start + 14],)]
    return b"".join(frames), colors


def validate_rgb(data):
    if not data or len(data) % REPORT_SIZE:
        raise DeviceError("Incomplete wireless RGB frame.")
    count = len(data) // REPORT_SIZE
    if count > 14:
        raise DeviceError("Wireless RGB frame exceeds fourteen fragments.")
    payload = bytearray()
    for sequence, start in enumerate(range(0, len(data), REPORT_SIZE)):
        frame = data[start:start + REPORT_SIZE]
        validate_report(frame)
        length = frame[4] - 0x10
        if frame[1:4] != bytes((0x88, count, sequence)) or not 1 <= length <= 14:
            raise DeviceError("Wireless RGB fragment header mismatch.")
        if (sequence < count - 1 and length != 14) or any(frame[5 + length:19]):
            raise DeviceError("Wireless RGB fragment has an invalid length or padding.")
        payload.extend(frame[5:5 + length])
    colors, offset = {}, 0
    while offset < len(payload):
        if len(payload) - offset < 4:
            raise DeviceError("Truncated wireless color group.")
        rgb, length = bytes(payload[offset:offset + 3]), payload[offset + 3]
        offset += 4
        if length == 0 or offset + length > len(payload):
            raise DeviceError("Invalid wireless color group length.")
        for index in payload[offset:offset + length]:
            if index >= 126 or index in colors:
                raise DeviceError("Invalid or duplicate wireless LED index.")
            colors[index] = rgb
        offset += length
    return colors


def report_sizes(descriptor):
    """Count report bits without relying on an interface number or usage page."""
    sizes, stack = {}, []
    report_id, bits, count = 0, 0, 0
    offset = 0
    while offset < len(descriptor):
        prefix = descriptor[offset]
        offset += 1
        if prefix == 0xFE:
            if offset + 2 > len(descriptor):
                raise ValueError("Truncated long HID item.")
            length = descriptor[offset]
            offset += 2 + length
            if offset > len(descriptor):
                raise ValueError("Truncated long HID item.")
            continue
        length = (0, 1, 2, 4)[prefix & 3]
        if offset + length > len(descriptor):
            raise ValueError("Truncated HID item.")
        value = int.from_bytes(descriptor[offset:offset + length], "little")
        offset += length
        tag = prefix & 0xFC
        if tag == 0x74:
            bits = value
        elif tag == 0x94:
            count = value
        elif tag == 0x84:
            report_id = value
        elif tag == 0xA4:
            stack.append((report_id, bits, count))
        elif tag == 0xB4:
            if not stack:
                raise ValueError("Unbalanced HID global pop.")
            report_id, bits, count = stack.pop()
        elif tag in {0x80, 0x90, 0xB0}:
            kind = {0x80: "input", 0x90: "output", 0xB0: "feature"}[tag]
            key = (kind, report_id)
            sizes[key] = sizes.get(key, 0) + bits * count
    return {key: (size + 7) // 8 + bool(key[1]) for key, size in sizes.items()}


def discover_receivers():
    paths = []
    for entry in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        device = (entry / "device").resolve()
        try:
            props = dict(line.split("=", 1) for line in
                         (device / "uevent").read_text().splitlines() if "=" in line)
            if props.get("HID_ID") != "0003:00003554:0000FA09":
                continue
            sizes = report_sizes((device / "report_descriptor").read_bytes())
            if sizes.get(("output", REPORT_ID)) == 20 and sizes.get(("input", REPORT_ID)) == 20:
                paths.append(Path("/dev") / entry.name)
        except (OSError, ValueError):
            continue
    return paths


class Receiver:
    def __init__(self, device=None):
        self.selected_device = Path(device) if device is not None else None
        self.fd = None
        self.last_sent = None

    def __enter__(self):
        paths = discover_receivers()
        if self.selected_device is not None:
            if self.selected_device not in paths:
                raise DeviceError("Select a wireless RGB interface returned by scan.")
            self.path = self.selected_device
        elif len(paths) == 1:
            self.path = paths[0]
        else:
            raise DeviceError(f"Found {len(paths)} wireless RGB interfaces. Use --device when there are several.")
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        except PermissionError as error:
            raise DeviceError(f"Receiver access denied for {self.path}. "
                              "Install packaging/70-chamak-chhallo.rules once, then reload udev rules "
                              "and unplug/replug the USB dongle. If the rule is already installed, "
                              "apply it to the connected receiver with: "
                              f"sudo udevadm trigger --action=add /sys/class/hidraw/{self.path.name}") from error
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.drain()
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def drain(self):
        for _ in range(64):
            try:
                if not os.read(self.fd, 256):
                    raise DeviceError("Wireless receiver disconnected.")
            except BlockingIOError:
                return
        raise DeviceError("Receiver input queue did not settle before the request.")

    def send(self, frame):
        validate_report(frame)
        if frame[1] != 0x44:
            raise DeviceError("This receiver connection currently permits config reads only.")
        self._write(frame)

    def _write(self, frame):
        if self.last_sent is not None:
            time.sleep(max(0, REPORT_GAP - (time.monotonic() - self.last_sent)))
        sent = os.write(self.fd, frame)
        self.last_sent = time.monotonic()
        if sent != len(frame):
            raise DeviceError(f"Short wireless output write: {sent}/{len(frame)} bytes.")

    def read(self, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                return None
            try:
                data = os.read(self.fd, 256)
            except BlockingIOError:
                continue
            if not data:
                raise DeviceError("Wireless receiver disconnected.")
            if data[0] != REPORT_ID:
                continue
            validate_report(data)
            return data

    def read_config(self):
        self.drain()
        request = report(0x44, 1, 0)
        self.send(request)
        frames = {}
        deadline = time.monotonic() + 2
        while len(frames) < CONFIG_COUNT:
            frame = self.read(deadline)
            if frame is None:
                raise DeviceError(f"Wireless config read timed out with {len(frames)}/10 fragments. "
                                  "Check that the keyboard is awake and in 2.4G mode.")
            if frame == request:
                continue
            if frame[1:3] != bytes((0x44, CONFIG_COUNT)) or frame[3] >= CONFIG_COUNT:
                continue
            sequence = frame[3]
            if sequence in frames and frames[sequence] != frame:
                raise DeviceError("Conflicting duplicate wireless config fragment.")
            frames[sequence] = frame
        ordered = tuple(frames[index] for index in range(CONFIG_COUNT))
        config_payload(ordered)
        return ordered
