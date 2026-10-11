"""Projection checks without screen capture, media playback, or real USB writes."""

import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import aula
from aula_aurora import LAYOUT, LiveSession
from aula_projection import ImageSource, Projection, ScreenSource, SIZE, VideoSource, stop_process
from test_aurora import StreamingKeyboard


class MemorySource(ImageSource):
    def __init__(self, color=(255, 80, 20)):
        super().__init__()
        self.publish(Image.new("RGB", SIZE, color))
        self.closed = False
        self.fail = threading.Event()

    def start(self):
        return self.image()

    def image(self):
        if self.fail.is_set():
            raise aula.DeviceError("simulated decoder failure")
        return super().image()

    def close(self):
        self.request_stop()
        self.closed = True


class MappingTests(unittest.TestCase):
    def test_uniform_source_brightness_packet_padding_and_unused_leds(self):
        projection = Projection(LAYOUT, MemorySource((200, 80, 20)))
        frame = projection.frame(0, "ignored", 2, 2)
        self.assertEqual(520, len(frame))
        self.assertEqual(bytes.fromhex("06 08 00 00 01 00 7a 01"), frame[:8])
        mapped = {p[0] for p in LAYOUT.values()}
        for index in range(122):
            expected = bytes((100, 40, 10)) if index in mapped else bytes(3)
            self.assertEqual(expected, frame[8 + index * 3:11 + index * 3])
        self.assertEqual(bytes(146), frame[374:])
        self.assertEqual(bytes(512), projection.frame(0, "ignored", 0, 2)[8:])

    def test_top_left_and_bottom_right_source_positions_reach_matching_keys(self):
        source = MemorySource()
        image = Image.new("RGB", SIZE)
        image.paste((255, 0, 0), (0, 0, 12, 5))
        image.paste((0, 0, 255), (60, 18, 72, 24))
        source.publish(image)
        frame = Projection(LAYOUT, source).frame(0, "ignored", 4, 2)
        for name, expected in (("ESC", (255, 0, 0)), ("RIGHT", (0, 0, 255))):
            offset = 8 + LAYOUT[name][0] * 3
            self.assertEqual(bytes(expected), frame[offset:offset + 3])

    def test_newest_frame_replaces_old_frame_without_a_backlog(self):
        source = MemorySource()
        projection = Projection(LAYOUT, source)
        for number in range(100):
            source.publish(Image.new("RGB", SIZE, (number, 0, 0)))
        frame = projection.frame(0, "ignored", 4, 2)
        self.assertEqual(bytes((99, 0, 0)), frame[8:11])

    def test_vividness_increases_color_separation_without_changing_peak_level(self):
        source = MemorySource((120, 100, 80))
        projection = Projection(LAYOUT, source)
        normal = projection.frame(0, "ignored", 4, 2, vividness=100)[8:11]
        vivid = projection.frame(0, "ignored", 4, 2, vividness=300)[8:11]
        self.assertEqual(bytes((120, 100, 80)), normal)
        self.assertEqual(max(normal), max(vivid))
        self.assertGreater(max(vivid) - min(vivid), max(normal) - min(normal))
        self.assertEqual((120, 100, 80), source.image().getpixel((0, 0)))
        dimmed = projection.frame(0, "ignored", 2, 2, vividness=300)[8:11]
        self.assertEqual(60, max(dimmed))

    def test_vividness_preserves_neutral_colors_black_and_zero_brightness(self):
        for value in (0, 70, 255):
            projection = Projection(LAYOUT, MemorySource((value,) * 3))
            for vividness in (0, 100, 300):
                frame = projection.frame(0, "ignored", 4, 2, vividness=vividness)
                self.assertEqual(bytes((value,) * 3), frame[8:11])
        projection = Projection(LAYOUT, MemorySource((120, 100, 80)))
        grayscale = projection.frame(0, "ignored", 4, 2, vividness=0)[8:11]
        self.assertEqual(grayscale[0], grayscale[1])
        self.assertEqual(grayscale[1], grayscale[2])
        self.assertEqual(bytes(512), projection.frame(0, "ignored", 0, 2, vividness=300)[8:])


class SourceTests(unittest.TestCase):
    def test_missing_video_is_rejected_without_starting_a_process(self):
        with patch("aula_projection.subprocess.Popen") as popen:
            with self.assertRaises(ValueError):
                VideoSource("/nonexistent/aula-projection-test.mp4")
        popen.assert_not_called()

    def test_screen_frame_is_decoded_in_memory_and_output_is_one_argument(self):
        buffer = io.BytesIO()
        Image.new("RGB", (12, 6), (20, 60, 100)).save(buffer, format="PPM")
        process = unittest.mock.Mock()
        process.communicate.return_value = (buffer.getvalue(), b"")
        process.returncode = 0
        process.poll.return_value = 0
        with patch("aula_projection.shutil.which", return_value="/usr/bin/grim"):
            source = ScreenSource("DP-1")
        publish = source.publish

        def one_frame(image):
            publish(image)
            source.stopped.set()

        source.publish = one_frame
        with patch("aula_projection.subprocess.Popen", return_value=process) as popen:
            source.produce()
        self.assertEqual((20, 60, 100), source.image().getpixel((0, 0)))
        self.assertEqual(SIZE, source.image().size)
        self.assertEqual(["/usr/bin/grim", "-t", "ppm", "-s", "0.1", "-o", "DP-1", "-"],
                         popen.call_args.args[0])

    def test_capture_failure_is_reported_instead_of_reusing_stale_frames(self):
        process = unittest.mock.Mock()
        process.communicate.return_value = (b"", b"unknown output")
        process.returncode = 1
        process.poll.return_value = 1
        with patch("aula_projection.shutil.which", return_value="/usr/bin/grim"):
            source = ScreenSource("wrong")
        with patch("aula_projection.subprocess.Popen", return_value=process):
            source._run()
        with self.assertRaisesRegex(aula.DeviceError, "unknown output"):
            source.image()

    def test_video_stream_packs_complete_rgb_frames_and_closes_the_pipe(self):
        process = unittest.mock.Mock()
        process.stdout = io.BytesIO(bytes((30, 70, 90)) * (SIZE[0] * SIZE[1]))
        process.poll.return_value = 0
        with tempfile.NamedTemporaryFile(suffix=".mp4") as file:
            with patch("aula_projection.shutil.which", return_value="/usr/bin/ffmpeg"):
                source = VideoSource(file.name)
            original_publish = source.publish

            def one_frame(image):
                original_publish(image)
                source.stopped.set()

            source.publish = one_frame
            with patch("aula_projection.subprocess.Popen", return_value=process) as popen:
                source.produce()
        self.assertEqual((30, 70, 90), source.image().getpixel((0, 0)))
        self.assertTrue(process.stdout.closed)
        args = popen.call_args.args[0]
        self.assertIn("-re", args)
        self.assertIn("-stream_loop", args)
        self.assertEqual("pipe:1", args[-1])

    def test_child_that_ignores_termination_is_killed_and_reaped(self):
        import subprocess
        process = unittest.mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("decoder", 1), 0]
        stop_process(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(2, process.wait.call_count)


class SessionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.keyboard = StreamingKeyboard()
        self.source = MemorySource()
        for override in [patch.object(aula, "BACKUPS", Path(directory.name) / "backups"),
                         patch("aula_aurora.Keyboard", return_value=self.keyboard),
                         patch("aula_projection.make_source", return_value=self.source)]:
            override.start()
            self.addCleanup(override.stop)

    def test_projection_updates_brightness_restores_settings_and_closes_source(self):
        config, palette = self.keyboard.read_config(), self.keyboard.read_palette()
        with patch("aula_aurora.KeyInput") as keys:
            session = LiveSession("00c8ff", 3, 2, effect="video", source="sample.mp4")
            try:
                result = session.start()
                self.assertEqual("video", result["effect"])
                self.assertEqual("sample.mp4", result["source"])
                self.assertEqual(self.keyboard.frames[-1], session.preview())
                session.update("00c8ff", 0, 2)
                self.assertTrue(self.keyboard.black_seen.wait(1))
                self.assertEqual([], self.keyboard.writes)
                self.assertEqual(1, len(list(aula.BACKUPS.iterdir())))
            finally:
                session.stop()
            keys.assert_not_called()
        self.assertTrue(self.source.closed)
        self.assertEqual(config, self.keyboard.read_config())
        self.assertEqual(palette, self.keyboard.read_palette())

    def test_live_vividness_change_keeps_capture_open_and_avoids_settings_writes(self):
        self.source.publish(Image.new("RGB", SIZE, (120, 100, 80)))
        expected = Projection(LAYOUT, self.source).frame(0, "ignored", 3, 2, vividness=300)
        vivid_seen = threading.Event()
        send = self.keyboard.send_direct

        def sent(frame):
            send(frame)
            if bytes(frame) == bytes(expected):
                vivid_seen.set()

        self.keyboard.send_direct = sent
        self.source.start = Mock(wraps=self.source.start)
        session = LiveSession("00c8ff", 3, 2, effect="screen")
        try:
            self.assertEqual(100, session.start()["vividness"])
            session.update("00c8ff", 3, 2, vividness=300)
            self.assertTrue(vivid_seen.wait(1))
            self.assertEqual(300, session.state()["vividness"])
            self.assertFalse(self.source.closed)
            self.source.start.assert_called_once()
            self.assertEqual([], self.keyboard.writes)
            self.assertEqual(1, len(list(aula.BACKUPS.iterdir())))
        finally:
            session.stop()

    def test_invalid_vividness_leaves_all_live_parameters_unchanged(self):
        session = LiveSession("00c8ff", 3, 2, effect="screen", vividness=180)
        for value in (-1, 301, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                session.update("ff0088", 4, 4, vividness=value)
            self.assertEqual(("00c8ff", 3, 2), session.settings)
            self.assertEqual(180, session.vividness)

    def test_decoder_failure_restores_settings_and_stops_source(self):
        config, palette = self.keyboard.read_config(), self.keyboard.read_palette()
        session = LiveSession("00c8ff", 3, 2, effect="screen")
        session.start()
        self.source.fail.set()
        session.thread.join(1)
        with self.assertRaisesRegex(aula.DeviceError, "simulated decoder failure"):
            session.stop()
        self.assertTrue(self.source.closed)
        self.assertEqual(config, self.keyboard.read_config())
        self.assertEqual(palette, self.keyboard.read_palette())

    def test_bad_source_startup_makes_no_usb_changes_or_backup(self):
        self.source.fail.set()
        session = LiveSession("00c8ff", 3, 2, effect="video", source="sample.mp4")
        with self.assertRaisesRegex(aula.DeviceError, "simulated decoder failure"):
            session.start()
        session.thread.join(1)
        self.assertEqual([], self.keyboard.frames)
        self.assertEqual([], self.keyboard.writes)
        self.assertFalse(aula.BACKUPS.exists())
        self.assertTrue(self.source.closed)

    def test_stopping_during_source_startup_cancels_without_usb_writes(self):
        class WaitingSource(ImageSource):
            def __init__(self):
                super().__init__()
                self.started = threading.Event()

            def produce(self):
                self.started.set()
                self.stopped.wait(2)

        source = WaitingSource()
        with patch("aula_projection.make_source", return_value=source):
            session = LiveSession("00c8ff", 3, 2, effect="screen")
        failures = []

        def start():
            try:
                session.start()
            except Exception as error:
                failures.append(error)

        starter = threading.Thread(target=start)
        starter.start()
        try:
            self.assertTrue(source.started.wait(1))
            session.stop()
        finally:
            source.request_stop()
            starter.join(2)
        self.assertFalse(starter.is_alive())
        self.assertFalse(source.thread.is_alive())
        self.assertFalse(session.running)
        self.assertEqual([], failures)
        self.assertEqual([], self.keyboard.frames)
        self.assertEqual([], self.keyboard.writes)


if __name__ == "__main__":
    unittest.main()
