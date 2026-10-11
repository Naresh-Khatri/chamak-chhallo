"""Select a wired keyboard or a wireless receiver without mixing their protocols."""

from dataclasses import dataclass
from pathlib import Path

from aula import DeviceError, Keyboard, discover_all
from aula_wireless import discover_receivers
from wireless_keyboard import WirelessKeyboard


def device_paths():
    return [option.path for option in device_options()]


@dataclass(frozen=True)
class DeviceOption:
    path: Path
    transport: str

    @property
    def label(self):
        connection = '2.4G dongle' if self.transport == 'wireless-2.4g' else 'Wired USB'
        return f'{connection} · {self.path.name}'


def device_options():
    return ([DeviceOption(path, 'wired-usb') for path in discover_all()]
            + [DeviceOption(path, 'wireless-2.4g') for path in discover_receivers()])


class DeviceSelection:
    def __init__(self, device=None, model=None):
        self.device = Path(device) if device is not None else None
        self.model = model
        self.options = []

    @property
    def wireless(self):
        return any(option.path == self.device and option.transport == 'wireless-2.4g'
                   for option in self.options)

    def refresh(self, options):
        previous = self.device
        self.options = list(options)
        paths = [option.path for option in self.options]
        if previous is not None and previous not in paths:
            self.device, self.model = None, None
        elif previous is None and len(paths) == 1:
            self.device = paths[0]
        if not self.wireless:
            self.model = None

    def choose(self, device):
        device = Path(device) if device is not None else None
        if device is not None and device not in [option.path for option in self.options]:
            raise DeviceError('Selected keyboard is no longer connected. Refresh the device list.')
        if device != self.device:
            self.model = None
        self.device = device

    def choose_model(self, model):
        if not self.wireless or model not in (None, 'f87'):
            raise DeviceError('Select an available wireless keyboard model.')
        self.model = model


def connect_keyboard(device=None, *, model=None):
    wired, wireless = discover_all(), discover_receivers()
    if device is None:
        paths = wired + wireless
        if len(paths) != 1:
            raise DeviceError(f'Found {len(paths)} RGB interfaces. Use scan and --device to select one.')
        device = paths[0]
    device = Path(device)
    if device in wired:
        return Keyboard(device)
    if device in wireless:
        return WirelessKeyboard(device, model=model)
    raise DeviceError('Select a supported RGB interface returned by scan.')
