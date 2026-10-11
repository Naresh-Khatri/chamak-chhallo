"""Continuous Wayland capture through the desktop portal and PipeWire."""

import os
import time
import uuid

from PIL import Image

from aula import DeviceError
from aula_projection import ImageSource, SCREEN_FPS, SIZE


DESTINATION = "org.freedesktop.portal.Desktop"
DESKTOP = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"


class ScreenCastPortal:
    def __init__(self, stopped):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.stopped = stopped
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.context = GLib.MainContext.new()
        self.session = None
        self.fd = None

    def call(self, path, interface, method, parameters=None):
        return self.connection.call_sync(DESTINATION, path, interface, method,
            parameters, None, self.Gio.DBusCallFlags.NONE, 2000, None)

    def request(self, method, prefix, options, timeout=60):
        GLib, Gio = self.GLib, self.Gio
        token = "aula_" + uuid.uuid4().hex
        options = {**options, "handle_token": GLib.Variant("s", token)}
        sender = self.connection.get_unique_name().lstrip(":").replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        response = []

        def responded(connection, sender, object_path, interface, signal, parameters, *_):
            if object_path == path:
                response.append(parameters.unpack())

        subscription = self.connection.signal_subscribe(DESTINATION,
            "org.freedesktop.portal.Request", "Response", None, None,
            Gio.DBusSignalFlags.NONE, responded)
        complete = False
        try:
            signature = {"CreateSession": "(a{sv})", "SelectSources": "(oa{sv})",
                         "Start": "(osa{sv})"}[method]
            result = self.call(DESKTOP, SCREENCAST, method,
                               GLib.Variant(signature, (*prefix, options)))
            path = result.unpack()[0]
            deadline = time.monotonic() + timeout
            while not response:
                if self.stopped.is_set():
                    raise InterruptedError("Screen selection was cancelled.")
                if time.monotonic() >= deadline:
                    raise DeviceError("Screen selection timed out. Select a display in the system picker.")
                while self.context.pending():
                    self.context.iteration(False)
                if not response:
                    self.stopped.wait(0.01)
            complete = True
            code, values = response[0]
            if code:
                raise DeviceError("Screen selection was cancelled." if code == 1 else
                                  "The desktop portal could not start screen capture.")
            return values
        finally:
            self.connection.signal_unsubscribe(subscription)
            if not complete:
                try:
                    self.call(path, "org.freedesktop.portal.Request", "Close")
                except Exception:
                    pass

    def open(self):
        GLib = self.GLib
        self.context.push_thread_default()
        try:
            options = {"session_handle_token": GLib.Variant("s", "aula_" + uuid.uuid4().hex)}
            self.session = self.request("CreateSession", (), options, timeout=5)["session_handle"]
            self.request("SelectSources", (self.session,),
                         {"types": GLib.Variant("u", 1), "multiple": GLib.Variant("b", False)},
                         timeout=5)
            streams = self.request("Start", (self.session, ""), {})["streams"]
            if not streams:
                raise DeviceError("The screen picker returned no display.")
            result, descriptors = self.connection.call_with_unix_fd_list_sync(
                DESTINATION, DESKTOP, SCREENCAST, "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self.session, {})), None,
                self.Gio.DBusCallFlags.NONE, 2000, None, None)
            self.fd = descriptors.get(result.unpack()[0])
            return self.fd, streams[0]
        finally:
            self.context.pop_thread_default()

    def close(self):
        try:
            if self.session:
                try:
                    self.call(self.session, "org.freedesktop.portal.Session", "Close")
                except self.GLib.Error:
                    pass
                self.session = None
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


def build_pipeline(Gst, fd, stream):
    pipeline = Gst.parse_launch(
        "pipewiresrc name=capture do-timestamp=true ! "
        "queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
        f"videorate drop-only=true max-rate={SCREEN_FPS} ! videoconvert ! videoscale add-borders=false ! "
        f"video/x-raw,format=RGB,width={SIZE[0]},height={SIZE[1]},pixel-aspect-ratio=1/1 ! "
        "appsink name=frames max-buffers=1 drop=true sync=false wait-on-eos=false")
    try:
        capture = pipeline.get_by_name("capture")
        capture.set_property("fd", fd)
        node, properties = stream
        serial = properties.get("pipewire-serial")
        if serial is not None and capture.find_property("target-object"):
            capture.set_property("target-object", str(serial))
        else:
            capture.set_property("path", str(node))
        if capture.find_property("on-disconnect"):
            capture.set_property("on-disconnect", 2)
        return pipeline
    except Exception:
        pipeline.set_state(Gst.State.NULL)
        raise


def sample_image(sample, Gst, GstVideo):
    info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
    if info is None or (info.width, info.height) != SIZE:
        raise DeviceError("Screen stream produced an unexpected image size.")
    buffer = sample.get_buffer()
    success, mapped = buffer.map(Gst.MapFlags.READ)
    if not success:
        raise DeviceError("Could not read a screen frame.")
    try:
        return Image.frombytes("RGB", SIZE, bytes(mapped.data), "raw", "RGB", info.stride[0], 1)
    finally:
        buffer.unmap(mapped)


class PortalScreenSource(ImageSource):
    startup_timeout = 75

    def __init__(self):
        super().__init__()
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gst, GstVideo
        Gst.init(None)
        self.Gst, self.GstVideo = Gst, GstVideo
        for name in ("pipewiresrc", "queue", "videorate", "videoconvert", "videoscale", "appsink"):
            if not Gst.ElementFactory.find(name):
                raise DeviceError(f"Smooth screen capture needs the GStreamer {name} plugin. "
                                  "Choose a screenshot fallback in Capture source.")

    def produce(self):
        portal, pipeline = None, None
        try:
            portal = ScreenCastPortal(self.stopped)
            fd, stream = portal.open()
            if self.stopped.is_set():
                return
            Gst = self.Gst
            pipeline = build_pipeline(Gst, fd, stream)
            sink, bus = pipeline.get_by_name("frames"), pipeline.get_bus()
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise DeviceError("PipeWire could not start the screen stream.")
            last_frame = time.monotonic()
            while not self.stopped.is_set():
                message = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if message:
                    if message.type == Gst.MessageType.ERROR:
                        error, _ = message.parse_error()
                        raise DeviceError(f"Screen stream failed: {error.message}")
                    raise DeviceError("Screen sharing ended.")
                sample = sink.emit("try-pull-sample", 100 * Gst.MSECOND)
                if sample:
                    self.publish(sample_image(sample, Gst, self.GstVideo))
                    last_frame = time.monotonic()
                elif self.latest is None and time.monotonic() - last_frame > 5:
                    raise DeviceError("PipeWire did not produce a screen frame.")
        finally:
            try:
                if pipeline:
                    pipeline.set_state(self.Gst.State.NULL)
            finally:
                if portal:
                    portal.close()
