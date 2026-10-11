"""Local video and Wayland screen sources for temporary per-key RGB frames."""

import io
import colorsys
from pathlib import Path
import shutil
import subprocess
import threading
import time
from collections import deque

from PIL import Image, ImageStat

from aula import DeviceError
from keyboard_profiles import WIRED_RGB


SIZE = (72, 24)
VIDEO_FPS = 25
SCREEN_FPS = 25
PROJECTION_EFFECTS = {"video", "screen"}


class ImageSource:
    startup_timeout = 4

    def __init__(self):
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.thread = None
        self.latest = None
        self.error = None
        self.process = None
        self.frame_times = deque(maxlen=120)

    def start(self):
        self.thread = threading.Thread(target=self._run, name="aula-image-source", daemon=True)
        self.thread.start()
        if not self.ready.wait(self.startup_timeout):
            self.close()
            raise DeviceError("The image source did not produce a frame before startup timed out.")
        if self.stopped.is_set():
            raise InterruptedError("Projection startup was cancelled.")
        return self.image()

    def publish(self, image):
        with self.lock:
            self.latest = image
            self.frame_times.append(time.monotonic())
        self.ready.set()

    def fps(self):
        cutoff = time.monotonic() - 1
        with self.lock:
            return sum(stamp > cutoff for stamp in self.frame_times)

    def image(self):
        with self.lock:
            if self.error:
                raise DeviceError(self.error)
            if self.latest is None:
                raise DeviceError("Waiting for the first image frame.")
            return self.latest

    def _run(self):
        try:
            self.produce()
        except Exception as error:
            if not self.stopped.is_set():
                with self.lock:
                    self.error = str(error)
        finally:
            self.ready.set()

    def request_stop(self):
        self.stopped.set()
        with self.lock:
            process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

    def close(self):
        self.request_stop()
        if self.thread:
            self.thread.join(3)
            if self.thread.is_alive():
                with self.lock:
                    process = self.process
                if process:
                    stop_process(process)
                self.thread.join(1)
            if self.thread.is_alive():
                raise DeviceError("The image source has not finished stopping.")


def stop_process(process):
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=1)


class VideoSource(ImageSource):
    def __init__(self, path):
        super().__init__()
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise ValueError("Choose an existing local video file.")
        self.ffmpeg = shutil.which("ffmpeg")
        if not self.ffmpeg:
            raise DeviceError("Video projection needs FFmpeg.")

    def produce(self):
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-stream_loop", "-1", "-re", "-i", str(self.path),
                   "-map", "0:v:0", "-an", "-sn", "-dn",
                   "-vf", f"fps={VIDEO_FPS},scale={SIZE[0]}:{SIZE[1]}:flags=area",
                   "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        with self.lock:
            self.process = process
        try:
            length = SIZE[0] * SIZE[1] * 3
            while not self.stopped.is_set():
                data = process.stdout.read(length)
                if len(data) != length:
                    raise DeviceError("FFmpeg stopped decoding. Check that the file has a supported video stream.")
                self.publish(Image.frombytes("RGB", SIZE, data))
        finally:
            stop_process(process)
            process.stdout.close()


class ScreenSource(ImageSource):
    def __init__(self, output=""):
        super().__init__()
        self.output = output
        self.grim = shutil.which("grim")
        if not self.grim:
            raise DeviceError("Live screen projection needs grim and a compatible Wayland compositor.")

    def produce(self):
        command = [self.grim, "-t", "ppm", "-s", "0.1"]
        if self.output:
            command.extend(["-o", self.output])
        command.append("-")
        while not self.stopped.is_set():
            started = time.monotonic()
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with self.lock:
                self.process = process
            try:
                data, error = process.communicate(timeout=2)
                if self.stopped.is_set():
                    return
                if process.returncode:
                    raise DeviceError("Screen capture failed: " + error.decode(errors="replace").strip()[:250])
                with Image.open(io.BytesIO(data)) as image:
                    self.publish(image.convert("RGB").resize(SIZE, Image.Resampling.BOX))
            finally:
                stop_process(process)
            self.stopped.wait(max(0, 1 / SCREEN_FPS - (time.monotonic() - started)))


class Projection:
    def __init__(self, layout, source):
        self.source = source
        xs, ys = [p[1] for p in layout.values()], [p[2] for p in layout.values()]
        left, top = min(xs) - 0.5, min(ys) - 0.5
        width, height = max(xs) - left + 0.5, max(ys) - top + 0.5
        self.samples = []
        for index, x, y in layout.values():
            x0 = max(0, int((x - 0.5 - left) / width * SIZE[0]))
            y0 = max(0, int((y - 0.45 - top) / height * SIZE[1]))
            x1 = min(SIZE[0], max(x0 + 1, round((x + 0.5 - left) / width * SIZE[0])))
            y1 = min(SIZE[1], max(y0 + 1, round((y + 0.45 - top) / height * SIZE[1])))
            self.samples.append((index, (x0, y0, x1, y1)))

    def press(self, *_):
        pass

    def render(self, now, color, brightness, speed, *, vividness=100):
        if not 0 <= brightness <= 4:
            raise ValueError("Brightness must be between 0 and 4.")
        if not 0 <= vividness <= 300:
            raise ValueError("Vividness must be between 0 and 300 percent.")
        image = self.source.image()
        colors = {}
        for index, area in self.samples:
            channels = ImageStat.Stat(image.crop(area)).mean
            if vividness != 100:
                hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in channels))
                channels = [channel * 255 for channel in colorsys.hsv_to_rgb(
                    hue, min(1, saturation * vividness / 100), value)]
            colors[index] = bytes(round(value * brightness / 4)
                                                       for value in channels)
        return colors

    def frame(self, now, color, brightness, speed, *, vividness=100):
        return WIRED_RGB.encode(self.render(now, color, brightness, speed, vividness=vividness))


def make_source(effect, source):
    if effect == "video":
        if not source:
            raise ValueError("Choose a video file before starting projection.")
        return VideoSource(source)
    if source is None:
        from aula_screencast import PortalScreenSource
        return PortalScreenSource()
    return ScreenSource(source)
