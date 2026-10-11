# Chamak Chhallo · चमक छल्लो

**AULA F87 RGB control for Linux: interactive animations, video projection, and Wayland screen capture.**

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
| Other identities | No validated profile; writes refused |
| 2.4 GHz dongle / Bluetooth | Unsupported |

Shared USB IDs do not identify models reliably. The app checks the complete identity before lighting writes. Only the owner's F87-family keyboard has been tested; this does not establish support for every retail revision. [F75 research](https://github.com/free-soldier28/Aula-Manager/blob/main/docs/PROTOCOL.md) and [streaming research](https://github.com/SibteProf/keylux/blob/main/docs/PROTOCOL.md) need hardware validation before enabling that profile.

## Setup

Linux, Python 3.10+, and a keyboard connected in wired USB mode. Clone this repository and open a terminal in its directory.

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

If USB access is denied:

```sh
sudo install -m 644 packaging/70-chamak-chhallo.rules /etc/udev/rules.d/70-chamak-chhallo.rules
sudo udevadm control --reload-rules
```

Reconnect the keyboard afterward. The rule grants the active desktop user access to hidraw interfaces for `258a:010c`. Run the app as your normal user. Typing reactions need access to the selected keyboard's evdev input interface; without it, animations still work. Input is never grabbed or recorded.

## Usage

```sh
python3 chamak_chhallo.py info
python3 chamak_chhallo.py set --color ff8000 --brightness 3
python3 chamak_chhallo.py animate --effect supernova --color ff0088
python3 chamak_chhallo.py project --video /path/to/video.mp4 --vividness 200
python3 chamak_chhallo.py project --screen --vividness 200
python3 chamak_chhallo.py restore /path/to/backup.json
```

Built-in effects: `off`, `solid`, `breathing`, `rainbow`, `ripple`, `starlight`, `reactive`. Brightness and speed accept 0–4. Add `--duration 10` to a live command for a ten-second run; otherwise Ctrl+C stops it. The old `python3 aula.py ...` commands still work.

With multiple keyboards, use `--device /dev/hidraw5` **before** the command, choosing the path returned by `scan`. Paths can change after reconnection. Close the panel before another CLI process takes control.

In the panel, choose a video file or use the screen capture picker. Video loops without audio. Vividness at 100% keeps source saturation, 0% gives grayscale, and up to 300% boosts it. Try 200%. Source images shrink to 72 × 24 pixels, so keys display colors and movement rather than readable text.

The F87 profile targets 25 FPS. Source and keyboard FPS are shown separately; an unchanged desktop may generate fewer source frames. If the portal is unavailable, `project --screen --screenshots` uses the `grim` fallback on compatible Wayland compositors. X11 capture is unverified. Images stay in memory and are never uploaded or saved.

## Backups

Each onboard change backs up settings, preserves unrelated bytes, and verifies read-back. Live effects use temporary frames and restore previous lighting on stop. Crashes, forced termination, or unplugging can prevent restoration; restore the saved backup afterward. Onboard changes can persist in keyboard flash; animation frames do not save settings repeatedly. The app does not flash firmware or remap keys.

New backups go to `$XDG_STATE_HOME/chamak-chhallo/backups`, defaulting to `~/.local/state/chamak-chhallo/backups`. Override with `CHAMAK_CHHALLO_BACKUPS`. Older version-1 JSON backups still work, including those in a previous checkout's `backups/` folder. Backups from other identities are refused.

## Development

```sh
python3 -m unittest -v
```

Tests use simulated USB, synthetic images, and mocked capture. GStreamer checks stay in the NULL state; no screen capture or GUI is started. GitHub CI is configured to run the suite on Ubuntu.

Layouts live in `keyboard_layouts.py`; model identities, settings offsets, codecs, and pacing live in `keyboard_profiles.py`. Effects render RGB dictionaries independently of USB encoding. See [protocol notes](docs/protocol.md). New profiles start read-only until their layout, commands, read-back, and restoration are verified on hardware. Wireless needs a separate transport.

For issues, include the exact underside model, connection mode, Linux desktop, `scan` output, and reproduction steps. Redact private paths and serial numbers; leave backups and captures out of commits.

[MIT license](LICENSE).
