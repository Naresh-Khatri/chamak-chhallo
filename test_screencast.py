"""Continuous capture checks without opening a screen picker or sharing a display."""

import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gio, GLib, Gst, GstVideo

from aula import DeviceError
from aula_projection import ImageSource, ScreenSource, SIZE, make_source
from aula_screencast import ScreenCastPortal, PortalScreenSource, build_pipeline, sample_image


Gst.init(None)


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.portal = ScreenCastPortal.__new__(ScreenCastPortal)
        self.portal.Gio, self.portal.GLib = Gio, GLib
        self.portal.stopped = threading.Event()
        self.portal.context = Mock()
        self.portal.context.pending.return_value = False
        self.portal.connection = Mock()
        self.portal.connection.get_unique_name.return_value = ":1.123"
        self.portal.connection.signal_subscribe.return_value = 7
        self.portal.session, self.portal.fd = None, None

    def respond(self, code, values):
        def call(path, interface, method, parameters=None):
            options = parameters.unpack()[-1]
            request = "/org/freedesktop/portal/desktop/request/1_123/" + options["handle_token"]
            callback = self.portal.connection.signal_subscribe.call_args.args[-1]
            callback(None, "portal", request, "request", "Response",
                     GLib.Variant("(ua{sv})", (code, values)))
            return GLib.Variant("(o)", (request,))
        self.portal.call = Mock(side_effect=call)

    def test_response_subscription_precedes_request_and_returns_session(self):
        self.respond(0, {"session_handle": GLib.Variant("s", "/session/aula")})
        result = self.portal.request("CreateSession", (), {})
        self.assertEqual("/session/aula", result["session_handle"])
        self.portal.connection.signal_unsubscribe.assert_called_once_with(7)

    def test_picker_cancellation_is_reported_and_unsubscribed(self):
        self.respond(1, {})
        with self.assertRaisesRegex(DeviceError, "cancelled"):
            self.portal.request("Start", ("/session/aula", ""), {})
        self.portal.connection.signal_unsubscribe.assert_called_once_with(7)

    def test_stop_closes_an_outstanding_picker_request(self):
        self.portal.call = Mock(return_value=GLib.Variant("(o)", ("/request/aula",)))
        self.portal.stopped.set()
        with self.assertRaises(InterruptedError):
            self.portal.request("Start", ("/session/aula", ""), {})
        self.assertEqual(("/request/aula", "org.freedesktop.portal.Request", "Close"),
                         self.portal.call.call_args.args)
        self.portal.connection.signal_unsubscribe.assert_called_once_with(7)

    def test_remote_fd_and_session_are_closed(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        self.portal.request = Mock(side_effect=[{"session_handle": "/session/aula"}, {},
            {"streams": [(123, {"pipewire-serial": 999})]}])
        descriptors = Mock()
        descriptors.get.return_value = read_fd
        self.portal.connection.call_with_unix_fd_list_sync.return_value = (
            GLib.Variant("(h)", (0,)), descriptors)
        self.portal.call = Mock()
        try:
            fd, stream = self.portal.open()
            self.assertEqual(read_fd, fd)
            self.assertEqual((123, {"pipewire-serial": 999}), stream)
        finally:
            self.portal.close()
        self.portal.context.push_thread_default.assert_called_once()
        self.portal.context.pop_thread_default.assert_called_once()
        self.portal.call.assert_called_with("/session/aula", "org.freedesktop.portal.Session", "Close")
        with self.assertRaises(OSError):
            os.fstat(read_fd)


class PipelineTests(unittest.TestCase):
    def test_installed_pipeline_drops_old_frames_and_uses_stream_serial(self):
        pipeline = build_pipeline(Gst, -1, (123, {"pipewire-serial": 999}))
        try:
            self.assertEqual("999", pipeline.get_by_name("capture").get_property("target-object"))
            sink = pipeline.get_by_name("frames")
            self.assertEqual(1, sink.get_property("max-buffers"))
            self.assertTrue(sink.get_property("drop"))
            self.assertFalse(sink.get_property("sync"))
        finally:
            pipeline.set_state(Gst.State.NULL)

    def test_old_portal_streams_use_node_id(self):
        pipeline = build_pipeline(Gst, -1, (123, {}))
        try:
            self.assertEqual("123", pipeline.get_by_name("capture").get_property("path"))
        finally:
            pipeline.set_state(Gst.State.NULL)

    def test_real_gstreamer_rgb_sample_decodes_without_starting_capture(self):
        caps = Gst.Caps.from_string(f"video/x-raw,format=RGB,width={SIZE[0]},height={SIZE[1]},framerate=25/1")
        buffer = Gst.Buffer.new_wrapped(bytes((25, 80, 180)) * (SIZE[0] * SIZE[1]))
        sample = Gst.Sample.new(buffer, caps, None, None)
        image = sample_image(sample, Gst, GstVideo)
        self.assertEqual(SIZE, image.size)
        self.assertEqual((25, 80, 180), image.getpixel((0, 0)))
        self.assertEqual((25, 80, 180), image.getpixel((71, 23)))

    def test_row_padding_is_ignored_and_buffer_is_unmapped(self):
        width, height = SIZE
        stride = width * 3 + 8
        data = (bytes((30, 90, 180)) * width + bytes(8)) * height
        buffer = Mock()
        buffer.map.return_value = (True, SimpleNamespace(data=data))
        sample = Mock()
        sample.get_buffer.return_value = buffer
        video = Mock()
        video.VideoInfo.new_from_caps.return_value = SimpleNamespace(width=width, height=height, stride=[stride])
        image = sample_image(sample, Gst, video)
        self.assertEqual((30, 90, 180), image.getpixel((71, 23)))
        buffer.unmap.assert_called_once()

    def test_portal_is_closed_when_stream_startup_fails(self):
        source = PortalScreenSource()
        portal = Mock()
        portal.open.side_effect = DeviceError("simulated portal failure")
        with patch("aula_screencast.ScreenCastPortal", return_value=portal):
            with self.assertRaisesRegex(DeviceError, "simulated portal failure"):
                source.produce()
        portal.close.assert_called_once()


class PacingTests(unittest.TestCase):
    def test_fallback_subtracts_capture_time_from_frame_interval(self):
        process = Mock()
        process.communicate.return_value = (b"synthetic ppm", b"")
        process.returncode = 0
        process.poll.return_value = 0
        with patch("aula_projection.shutil.which", return_value="/usr/bin/grim"):
            source = ScreenSource()
        source.publish = Mock()
        source.stopped.wait = Mock(side_effect=lambda timeout: source.stopped.set())
        image = MagicMock()
        with patch("aula_projection.subprocess.Popen", return_value=process), \
             patch("aula_projection.Image.open", return_value=image), \
             patch("aula_projection.time.monotonic", side_effect=[0, 0.025]):
            source.produce()
        self.assertAlmostEqual(0.015, source.stopped.wait.call_args.args[0])

    def test_fps_counts_source_frames_instead_of_usb_repeats(self):
        source = ImageSource()
        source.frame_times.extend([0.5, 1.25, 1.5, 1.9])
        with patch("aula_projection.time.monotonic", return_value=2):
            self.assertEqual(3, source.fps())
        with patch("aula_projection.time.monotonic", return_value=4):
            self.assertEqual(0, source.fps())

    def test_default_uses_streaming_and_explicit_output_uses_screenshots(self):
        with patch("aula_screencast.PortalScreenSource") as stream:
            self.assertEqual(stream.return_value, make_source("screen", None))
        with patch("aula_projection.ScreenSource") as screenshots:
            self.assertEqual(screenshots.return_value, make_source("screen", "DP-1"))
            screenshots.assert_called_once_with("DP-1")


if __name__ == "__main__":
    unittest.main()
