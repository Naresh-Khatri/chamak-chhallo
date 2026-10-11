"""Exercise GUI callbacks with simulated widgets, without starting GTK or USB."""

from collections import deque
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

import aula_gui
from keyboard_device import DeviceOption
from keyboard_profiles import F87


class Widget:
    def __init__(self, **kwargs):
        self.properties = kwargs
        self.signals = {}
        self.selected = 0
        self.model = kwargs.get('model')
        self.model_changes = 0
        self.notifying = False
        self.text = kwargs.get('text', '')
        self.value = 0
        self.css_classes = set()

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def connect(self, name, callback):
        self.signals.setdefault(name, []).append(callback)

    def emit(self, name):
        for callback in self.signals.get(name, []):
            callback(self)

    def set_model(self, model):
        if self.notifying:
            raise AssertionError('Dropdown model replaced during selection notification')
        self.model = model
        self.model_changes += 1
        self.set_selected(0)

    def set_sensitive(self, value):
        if self.notifying:
            raise AssertionError('Dropdown sensitivity changed during selection notification')
        self.properties['sensitive'] = value

    def set_selected(self, index):
        if index == self.selected:
            return
        self.selected = index
        self.notifying = True
        try:
            self.emit('notify::selected')
        finally:
            self.notifying = False

    def get_selected(self):
        return self.selected

    def set_text(self, text):
        self.text = text
        self.emit('changed')

    def get_text(self):
        return self.text

    def set_value(self, value):
        self.value = value
        self.emit('value-changed')

    def get_value(self):
        return self.value

    def get_value_as_int(self):
        return int(self.value)

    def add_css_class(self, name):
        self.css_classes.add(name)

    def remove_css_class(self, name):
        self.css_classes.discard(name)


class ImmediateExecutor:
    def __init__(self, **kwargs):
        pass

    def submit(self, operation):
        future = Future()
        try:
            future.set_result(operation())
        except Exception as error:
            future.set_exception(error)
        return future

    def shutdown(self, **kwargs):
        pass


class SelectorCallbackTests(unittest.TestCase):
    def test_select_switch_and_rescan_wait_until_notification_returns(self):
        widgets, idle, events = {}, deque(), []
        wired = DeviceOption(Path('/dev/hidraw7'), 'wired-usb')
        radio = DeviceOption(Path('/dev/hidraw5'), 'wireless-2.4g')

        def make(kind):
            def constructor(**kwargs):
                widget = Widget(**kwargs)
                widgets.setdefault(kind, []).append(widget)
                return widget
            return constructor

        def drain():
            for _ in range(30):
                if not idle:
                    return
                callback, args = idle.popleft()
                callback(*args)
            self.fail('GUI callbacks did not settle')

        def schedule(callback, *args):
            idle.append((callback, args))
            return len(idle)

        def state(path, model):
            wireless = path == radio.path
            return {'device': str(path), 'transport': 'wireless-2.4g' if wireless else 'wired-usb',
                    'model': 'Simulated keyboard', 'writable': not wireless or model == 'f87',
                    'effect': 'reactive', 'color': None if wireless else '#00ffff',
                    'brightness': 3, 'speed': 2}

        class KeyboardContext:
            def __init__(self, path, model):
                self.path, self.model = path, model

            def __enter__(self):
                events.append(('connect', self.path, self.model))
                return SimpleNamespace(state=lambda: state(self.path, self.model))

            def __exit__(self, *_):
                pass

        class Session:
            effect = 'aurora'
            source_name = None
            profile = F87

            def __init__(self, *args, device, model, **kwargs):
                self.device, self.model = device, model
                self.running = True
                self.stopped = threading.Event()

            def start(self):
                return {**state(self.device, self.model), 'effect': 'aurora', 'streaming': True,
                        'typing_reactions': False, 'backup': '/tmp/simulated-backup.json'}

            def request_stop(self):
                self.stopped.set()

            def stop(self):
                events.append(('stop', self.device))
                self.running = False

        class Application(Widget):
            def run(self, argv):
                self.emit('activate')
                drain()
                dropdown = widgets['DropDown'][0]
                self_test.assertEqual([], events)
                dropdown.set_selected(2)
                self_test.assertEqual([], events)
                drain()
                self_test.assertEqual(('connect', radio.path, None), events[-1])
                self_test.assertEqual(1, dropdown.model_changes)

                model_row = next(row for row in widgets['ComboRow'] if row.properties['title'] == 'Paired keyboard')
                before = len(events)
                model_row.set_selected(1)
                self_test.assertEqual(before, len(events))
                drain()
                self_test.assertEqual(('connect', radio.path, 'f87'), events[-1])
                self_test.assertEqual(1, dropdown.model_changes)

                effect = next(row for row in widgets['ComboRow'] if row.properties['title'] == 'Effect')
                effect.set_selected(effect.model.index('Aurora + key ripples'))
                drain()
                dropdown.set_selected(1)
                self_test.assertNotEqual(('stop', radio.path), events[-1])
                drain()
                self_test.assertEqual([('stop', radio.path), ('connect', wired.path, None)], events[-2:])
                self_test.assertEqual(1, dropdown.model_changes)
                restore = next(button for button in widgets['Button']
                               if button.properties.get('label') == 'Restore previous settings')
                self_test.assertFalse(restore.properties['sensitive'])

                rescan = next(button for button in widgets['Button']
                              if button.properties.get('icon_name') == 'view-refresh-symbolic')
                rescan.emit('clicked')
                self_test.assertEqual(1, dropdown.model_changes)
                drain()
                self_test.assertEqual(2, dropdown.model_changes)
                status = next(label for label in widgets['Label'] if label.properties.get('selectable'))
                self_test.assertNotIn('error', status.css_classes)
                return 0

        self_test = self
        gtk = SimpleNamespace(**{kind: make(kind) for kind in
            ('Box', 'DropDown', 'Button', 'ScrolledWindow', 'Label', 'Entry', 'ColorButton', 'DrawingArea')},
            StringList=SimpleNamespace(new=list),
            SpinButton=SimpleNamespace(new_with_range=lambda *args: make('SpinButton')()),
            Scale=SimpleNamespace(new_with_range=lambda *args: make('Scale')()),
            Orientation=SimpleNamespace(VERTICAL=0, HORIZONTAL=1),
            Align=SimpleNamespace(CENTER=0), PositionType=SimpleNamespace(RIGHT=0, BOTTOM=1),
            AccessibleProperty=SimpleNamespace(LABEL=0))
        adw = SimpleNamespace(Application=Application, **{kind: make(kind) for kind in
            ('ApplicationWindow', 'HeaderBar', 'Clamp', 'PreferencesGroup', 'ComboRow', 'ActionRow')})
        display = SimpleNamespace(get_monitors=lambda: SimpleNamespace(get_n_items=lambda: 0))
        gdk = SimpleNamespace(Display=SimpleNamespace(get_default=lambda: display),
                              RGBA=lambda: SimpleNamespace(parse=lambda text: True))
        glib = SimpleNamespace(idle_add=schedule, timeout_add=lambda *args: 0,
                               SOURCE_REMOVE=False, SOURCE_CONTINUE=True)
        with patch.object(aula_gui, 'Gtk', gtk), patch.object(aula_gui, 'Adw', adw), \
                patch.object(aula_gui, 'Gdk', gdk), patch.object(aula_gui, 'GLib', glib), \
                patch.object(aula_gui, 'ThreadPoolExecutor', ImmediateExecutor), \
                patch.object(aula_gui, 'device_options', return_value=[wired, radio]), \
                patch.object(aula_gui, 'Keyboard', side_effect=lambda path, model: KeyboardContext(path, model)), \
                patch.object(aula_gui, 'LiveSession', Session):
            self.assertEqual(0, aula_gui.run(model='f87'))


if __name__ == '__main__':
    unittest.main()
