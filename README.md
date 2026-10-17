# Chamak Chhallo · चमक छल्लो

**AULA F75/F87 RGB control for Linux: interactive animations, video projection, and Wayland screen capture.**

A keyboard with main-character energy. Pick a color, turn typing into a galaxy, or let a movie paint the keys. Native GTK4 app and Python CLI; no vendor software or OpenRGB dependency.

## Features

- Solid color, brightness, and built-in lighting effects. Changes apply immediately.
- **Aurora** with keypress ripples and sparkling curtains of color.
- **Supernova** with typing-powered comets. Space explodes; Backspace reverses the spin.
- Video and live screen projection, keyboard preview, FPS counters, and a vividness slider.
- Settings backups, exact read-back, and restoration when live effects stop.

## Hardware support

| Device | Status |
| --- | --- |
| Wired AULA F87-family, identity `03 00 00 00 01 0b` | Lighting and restoration tested; cyan visually confirmed |
| Wired AULA F75, reported identity `03 00 00 00 03 66` | Recognized read-only; lighting not implemented |
| Wired AULA F75, identity `03 00 00 00 00 cd` | 20 FPS USB streaming measured; settings restoration verified by read-back |
| Other identities | No validated profile; writes refused |
| F87 via `3554:fa09` 2.4 GHz dongle | Experimental transport; hardware validation pending |
| F75 via `3554:fa09` 2.4 GHz dongle | Onboard effects, custom colors, and live streaming; steady cyan visually confirmed, restoration checked by read-back |
| Bluetooth | Unsupported |

Shared USB IDs do not identify models reliably. The app checks the complete wired identity before lighting writes. Hardware checks do not establish support for every retail revision. F75 onboard settings use the fields documented in [Aula-Manager](https://github.com/free-soldier28/Aula-Manager/blob/main/src/Aula.Core/Models/ModelConfig.cs). The F75 has its own 80-key LED map and streaming profile. USB targets 20 FPS; the F75 dongle targets 8 FPS with a coarser palette and a 64-report/second budget. Steady wireless cyan was visually confirmed on the connected F75, and Aurora streaming plus restoration passed hardware checks.

## Setup

Linux and Python 3.10+. Clone this repository and open a terminal in its directory. Wired USB is tested; the dongle path is experimental.

**Arch Linux**

```sh
sudo pacman -S python python-pillow python-gobject python-cairo gtk4 libadwaita ffmpeg gstreamer gst-plugins-base gst-plugin-pipewire
```

**Ubuntu 24.04 / Debian**

```sh
sudo apt install python3 python3-pil python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base gstreamer1.0-pipewire ffmpeg
```

Optional: `python-evdev` on Arch or `python3-evdev` on Ubuntu for typing reactions; `grim` for screenshot fallback. Package names may vary by release. Smooth screen capture needs a working Wayland ScreenCast portal and PipeWire integration.

```sh
python3 chamak_chhallo.py scan
python3 chamak_chhallo.py gui
```

Use system Python to access GTK/GStreamer bindings. For a virtual environment, create it with `python3 -m venv --system-site-packages .venv`. An optional `pip install -e .` installs the `chamak-chhallo` command.

If USB access is denied, install the permission rule **once per Linux machine**:

```sh
sudo install -m 644 packaging/70-chamak-chhallo.rules /etc/udev/rules.d/70-chamak-chhallo.rules
sudo udevadm control --reload-rules
```

Reconnect the keyboard or dongle afterward. The rule survives reboots, app updates, and reinstalling or cloning the project. Install it again only if you reinstall Linux or the project's rule changes.

Reloading rules does not update permissions on an already-connected device. If access is still denied, physically unplug and reinsert the USB dongle; switching the keyboard's mode is not enough. Alternatively, use the receiver path shown in the error to apply the rule without unplugging, for example `sudo udevadm trigger --action=add /sys/class/hidraw/hidraw7`, then run `udevadm settle`. The `hidraw` number can change after reconnection.

The rule grants the active desktop user access to hidraw interfaces for wired `258a:010c` and receiver `3554:fa09`. Run the app as your normal user. Typing reactions need access to the selected keyboard's evdev input interface; without it, animations still work. Input is never grabbed or recorded.

## Usage

```sh
python3 chamak_chhallo.py info
python3 chamak_chhallo.py set --color ff8000 --brightness 3
python3 chamak_chhallo.py animate --effect supernova --color ff0088
python3 chamak_chhallo.py project --video /path/to/video.mp4 --vividness 200
python3 chamak_chhallo.py project --screen --vividness 200
python3 chamak_chhallo.py restore /path/to/backup.json
```

Shared built-in effects: `off`, `solid`, `breathing`, `rainbow`, `ripple`, `starlight`, `reactive`. F75 also exposes its remaining onboard effects, including `marquee`, `onboard-aurora`, `gradient`, and `rainbow-wave`. F87 brightness and all speed controls accept 0–4; supported F75 onboard profiles accept brightness 0–9. Live animation brightness remains 0–4. Add `--duration 10` to a live command for a ten-second run; otherwise Ctrl+C stops it. The old `python3 aula.py ...` commands still work.

The panel's top bar lets you choose **Wired USB** or a **2.4G dongle**. If multiple interfaces are connected, choose one there. Use the refresh icon after connecting or removing a device. Switching devices stops the current live effect and restores its settings before opening the new connection.

For CLI commands with multiple keyboards, use `--device /dev/hidraw5` **before** the command, choosing the path returned by `scan`. Paths can change after reconnection. Close the panel before another CLI process takes control.

In the panel, choose a video file or use the screen capture picker. Video loops without audio. Vividness at 100% keeps source saturation, 0% gives grayscale, and up to 300% boosts it. Try 200%. Source images shrink to 72 × 24 pixels, so keys display colors and movement rather than readable text.

The F87 USB profile targets 25 FPS; F75 USB targets 20 FPS, F75 wireless targets 8 FPS, and F87 wireless targets 4 FPS. Source and keyboard FPS are shown separately; an unchanged desktop may generate fewer source frames. If the portal is unavailable, `project --screen --screenshots` uses the `grim` fallback on compatible Wayland compositors. X11 capture is unverified. Images stay in memory and are never uploaded or saved.

The connected F75 measured 200 USB Aurora frames in ten seconds (20 FPS), then restored the original config and palette exactly. A 30 FPS host-streaming rate is beyond the [documented F75 timing envelope](https://github.com/SibteProf/keylux/blob/main/docs/PROTOCOL.md#timing); 25 FPS has been reported to drop or duplicate keypresses. The panel displays measured and target FPS during live effects.

## Experimental 2.4 GHz

Switch the keyboard to 2.4G, connect its paired dongle, and check that typing works. In the panel, select the dongle in the top bar, then choose **AULA F87 (experimental)** or **AULA F75 (experimental)** under **Paired keyboard**, matching your keyboard. Launch with `python3 chamak_chhallo.py gui`, or preselect F75 with `python3 chamak_chhallo.py --model f75 gui`. The receiver cannot identify the paired model, so CLI lighting commands require explicit `--model f87` or `--model f75`. On the connected F75, a breathing effect at brightness 3 and speed 2 was applied, read back, and restored to the exact original settings. Wireless solid cyan was visually confirmed after correcting the refresh interval, and Aurora streamed at 4 FPS across 80 mapped keys. Native settings matched their backup after both tests.

Config fragments require exact acknowledgements. Some receivers do not echo the final save command; the app requires matching full configuration read-back before accepting that case. Read-back confirms the current configuration; persistence across a power cycle has not been tested.

```sh
python3 chamak_chhallo.py scan
python3 chamak_chhallo.py --model f87 info
python3 chamak_chhallo.py --model f87 gui
python3 chamak_chhallo.py --model f87 animate --effect wireless-solid --color 00ffff
python3 chamak_chhallo.py --model f87 project --video /path/to/video.mp4
python3 chamak_chhallo.py --model f87 project --screen
```

For F75 and F87, the panel offers **Solid color over dongle**. Keep it open for custom colors. F75 wireless streaming targets 8 FPS with at most eight reports per frame and 64 reports/second; F87 targets 4 FPS with at most fourteen reports per frame. The encoder groups equal colors and reduces color detail when a complete frame exceeds its packet budget. The 8 FPS target has simulated packet-budget coverage and still needs hardware validation; the earlier 4 FPS setting was measured on hardware. F75 streaming enters per-key mode by patching only lighting fields in the receiver's own saved configuration, then restores that configuration on stop. Unchanged F75 colors need frequent refreshes to stay steady. Firmware effects may repaint over F87 streamed colors; that path still needs hardware validation. Built-in effect changes use the existing palette; custom saved palette colors cannot be read or changed through this implementation. Existing custom per-key mode prevents streaming because its table cannot be backed up. [Wireless config research](https://github.com/marcoslor/Aula-F87-Controller/blob/main/docs/PROTOCOL.md) and [RGB streaming research](https://github.com/SibteProf/keylux/blob/main/docs/PROTOCOL.md).

## Backups

Each onboard change backs up settings, preserves unrelated bytes, and verifies read-back. Live effects use temporary frames and restore previous lighting on stop. Crashes, forced termination, or unplugging can prevent restoration; restore the saved backup afterward. Onboard changes can persist in keyboard flash; animation frames do not save settings repeatedly. The app does not flash firmware or remap keys.

New backups go to `$XDG_STATE_HOME/chamak-chhallo/backups`, defaulting to `~/.local/state/chamak-chhallo/backups`. Override with `CHAMAK_CHHALLO_BACKUPS`. Older version-1 wired JSON backups still work, including those in a previous checkout's `backups/` folder. Wireless snapshots use version 2 and require the same `--model f75` or `--model f87` selection used when creating them. Transports are never mixed. A wireless settings fingerprint guards unrelated bytes; it is not a unique keyboard identity, and a receiver must remain paired with the same keyboard during a session.

## Development

```sh
python3 -m unittest -v
```

Tests use simulated USB, synthetic images, and mocked capture. GStreamer checks stay in the NULL state; no screen capture or GUI is started. GitHub CI is configured to run the suite on Ubuntu.

Layouts live in `keyboard_layouts.py`; wired profiles live in `keyboard_profiles.py`. Effects render RGB dictionaries independently of USB encoding. `keyboard_device.py` selects the transport; `aula_wireless.py` frames receiver packets and `wireless_keyboard.py` implements experimental F75 and F87 operations. See [protocol notes](docs/protocol.md). New model profiles start read-only until their layout, commands, read-back, and restoration are verified on hardware.

For issues, include the exact underside model, connection mode, Linux desktop, `scan` output, and reproduction steps. Redact private paths and serial numbers; leave backups and captures out of commits.

[MIT license](LICENSE).
