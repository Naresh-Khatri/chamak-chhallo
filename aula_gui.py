"""Native controls using the GTK4 and libadwaita already installed on this machine."""

from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from aula import ANIMATED_EFFECTS, EFFECT_LABELS, parse_color
from keyboard_device import DeviceSelection, device_options, connect_keyboard as Keyboard
from aula_live import LatestChangeQueue
from aula_aurora import LIVE_HINTS, LIVE_LABELS, LAYOUT, LiveSession


PROJECTIONS = {"video", "screen"}


def panel_effects(transport):
    labels = {**EFFECT_LABELS, **LIVE_LABELS}
    labels.pop("solid" if transport == "wireless-2.4g" else "wireless-solid")
    return labels


def projection_status(result):
    return (f"{LIVE_LABELS[result['effect']]} is live.\n{result['source']}\n"
            f"Source: {result['capture_fps']} FPS · Keyboard: {result['usb_fps']} FPS\n"
            "Frames stay local and are never saved.")


def draw_keyboard(context, width, height, colors, layout=LAYOUT):
    context.set_source_rgb(0.06, 0.07, 0.09)
    context.paint()
    xs, ys = [p[1] for p in layout.values()], [p[2] for p in layout.values()]
    span_x, span_y = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    scale = min((width - 20) / span_x, (height - 20) / span_y)
    left, top = (width - span_x * scale) / 2, (height - span_y * scale) / 2
    for index, x, y in layout.values():
        rgb = colors.get(index, (35, 39, 46)) if colors else (35, 39, 46)
        context.set_source_rgb(*(value / 255 for value in rgb))
        context.rectangle(left + (x - min(xs) + 0.1) * scale, top + (y - min(ys) + 0.1) * scale,
                          0.8 * scale, 0.8 * scale)
        context.fill()


def run(device=None, model=None):
    app = Adw.Application(application_id="local.chamakchhallo.Lighting")
    executor = ThreadPoolExecutor(max_workers=1)
    active_session = [None]
    connection = DeviceSelection(device, model)

    def stop_stream():
        session, active_session[0] = active_session[0], None
        if session:
            session.stop()

    def call_keyboard(operation):
        stop_stream()
        if connection.device is None:
            return {"selection_required": True}
        with Keyboard(connection.device, model=connection.model) as keyboard:
            return operation(keyboard)

    def read_state():
        session = active_session[0]
        if session and session.running and not session.stopped.is_set():
            return session.state()
        return call_keyboard(lambda keyboard: keyboard.state())

    def activate(application):
        if application.get_active_window():
            application.get_active_window().present()
            return
        window = Adw.ApplicationWindow(application=application, title="Chamak Chhallo")
        window.set_default_size(500, 580)
        window.set_size_request(380, 480)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        devices = Gtk.DropDown(model=Gtk.StringList.new(["Choose keyboard…"]))
        devices.set_tooltip_text("Choose the keyboard or wireless dongle to control")
        devices.update_property([Gtk.AccessibleProperty.LABEL], ["Keyboard connection"])
        header.set_title_widget(devices)
        rescan = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh connected keyboards")
        header.pack_end(rescan)
        outer.append(header)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20,
                       margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        clamp = Adw.Clamp(maximum_size=600, child=page)
        scroll = Gtk.ScrolledWindow(vexpand=True, child=clamp)
        outer.append(scroll)
        window.set_content(outer)

        title = Gtk.Label(label="Chamak Chhallo", xalign=0)
        title.add_css_class("title-1")
        page.append(title)
        description = Gtk.Label(label="Changes apply automatically as you adjust the controls.",
                                xalign=0, wrap=True)
        page.append(description)
        connection_group = Adw.PreferencesGroup()
        paired_model = Adw.ComboRow(title="Paired keyboard",
            subtitle="The dongle cannot detect its model. Select the keyboard you own.",
            model=Gtk.StringList.new(["Read-only inspection", "AULA F87 (experimental)"]))
        connection_group.add(paired_model)
        connection_group.set_visible(False)
        page.append(connection_group)
        group = Adw.PreferencesGroup()
        page.append(group)
        labels = panel_effects("wired-usb")
        effect_names = list(labels)
        effect = Adw.ComboRow(title="Effect", model=Gtk.StringList.new(list(labels.values())))
        group.add(effect)
        video_path = [None]
        video_dialog = [None]
        video_button = Gtk.Button(label="Choose video…", valign=Gtk.Align.CENTER)
        video_row = Adw.ActionRow(title="Video file", subtitle="Local files play on a loop")
        video_row.add_suffix(video_button)
        group.add(video_row)
        monitors = Gdk.Display.get_default().get_monitors()
        outputs = [None, ""]
        for number in range(monitors.get_n_items()):
            connector = monitors.get_item(number).get_connector()
            if connector and connector not in outputs:
                outputs.append(connector)
        screen_row = Adw.ComboRow(title="Capture source",
            subtitle="Smooth capture opens the system display picker",
            model=Gtk.StringList.new(["Smooth capture (system picker)",
                                     "All displays (screenshots)",
                                     *[f"{name} (screenshots)" for name in outputs[2:]]]))
        group.add(screen_row)
        color = Gtk.Entry(text="#00c8ff", width_chars=9, max_length=7,
                          valign=Gtk.Align.CENTER)
        color.set_tooltip_text("Six-digit RGB hex color")
        color.update_property([Gtk.AccessibleProperty.LABEL], ["RGB color"])
        picker = Gtk.ColorButton(valign=Gtk.Align.CENTER)
        rgba = Gdk.RGBA()
        rgba.parse("#00c8ff")
        picker.set_rgba(rgba)
        row = Adw.ActionRow(title="Color", subtitle="Used by solid color")
        row.add_suffix(color)
        row.add_suffix(picker)
        group.add(row)

        def picked(button):
            chosen = button.get_rgba()
            color.set_text("#" + "".join(f"{round(component * 255):02x}" for component in
                                        (chosen.red, chosen.green, chosen.blue)))

        picker.connect("color-set", picked)

        def changed(entry):
            try:
                parse_color(entry.get_text())
                value = Gdk.RGBA()
                value.parse(entry.get_text() if entry.get_text().startswith("#")
                            else "#" + entry.get_text())
                picker.set_rgba(value)
                entry.remove_css_class("error")
            except ValueError:
                entry.add_css_class("error")

        color.connect("changed", changed)
        brightness = Gtk.SpinButton.new_with_range(0, 4, 1)
        brightness.set_value(3)
        brightness.set_valign(Gtk.Align.CENTER)
        bright_row = Adw.ActionRow(title="Brightness", subtitle="0 turns the backlight off; 4 is brightest")
        bright_row.add_suffix(brightness)
        bright_row.set_activatable_widget(brightness)
        group.add(bright_row)
        vividness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 300, 1)
        vividness.set_value(100)
        vividness.set_digits(0)
        vividness.set_draw_value(True)
        vividness.set_value_pos(Gtk.PositionType.RIGHT)
        vividness.set_format_value_func(lambda scale, value, *_: f"{value:.0f}%")
        vividness.set_size_request(200, -1)
        vividness.set_valign(Gtk.Align.CENTER)
        vividness.update_property([Gtk.AccessibleProperty.LABEL], ["Projection vividness"])
        vividness.add_mark(100, Gtk.PositionType.BOTTOM, None)
        vivid_row = Adw.ActionRow(title="Vividness", subtitle="100% keeps source colors; raise for stronger color")
        vivid_row.add_suffix(vividness)
        vivid_row.set_activatable_widget(vividness)
        group.add(vivid_row)
        speed = Gtk.SpinButton.new_with_range(0, 4, 1)
        speed.set_value(2)
        speed.set_valign(Gtk.Align.CENTER)
        speed_row = Adw.ActionRow(title="Speed", subtitle="Used by animated effects")
        speed_row.add_suffix(speed)
        speed_row.set_activatable_widget(speed)
        group.add(speed_row)
        preview_label = Gtk.Label(label="Keyboard preview", xalign=0)
        page.append(preview_label)
        preview = Gtk.DrawingArea(content_height=160, hexpand=True)
        preview.set_tooltip_text("Colors sent to the keyboard, arranged by key position")
        preview.set_draw_func(lambda _, context, width, height: draw_keyboard(
            context, width, height, active_session[0].preview_colors() if active_session[0] else None,
            active_session[0].profile.layout if active_session[0] and active_session[0].profile else LAYOUT))
        page.append(preview)
        refresh = Gtk.Button(label="Read keyboard")
        page.append(refresh)
        restore = Gtk.Button(label="Restore previous settings", sensitive=False)
        page.append(restore)
        status = Gtk.Label(label="Reading keyboard…", xalign=0, wrap=True, selectable=True)
        page.append(status)
        note = Gtk.Label(label="Settings stay on the keyboard. Each change saves a local backup.",
                         xalign=0, wrap=True)
        note.add_css_class("dim-label")
        page.append(note)
        last_backup = [None]
        busy = [False]
        syncing = [True]
        closing = [False]
        writable = [False]
        wireless = [False]
        selecting = [False]
        connection_pending = [False]
        rebuild_pending = [False]

        def update_connection_controls(rebuild=False):
            selecting[0] = True
            try:
                if rebuild:
                    devices.set_model(Gtk.StringList.new(["Choose keyboard…", *[item.label for item in connection.options]]))
                paths = [item.path for item in connection.options]
                devices.set_selected(paths.index(connection.device) + 1 if connection.device in paths else 0)
                connection_group.set_visible(connection.wireless)
                paired_model.set_selected(1 if connection.model == 'f87' else 0)
                devices.set_tooltip_text(str(connection.device) if connection.device else
                                         "Choose the keyboard or wireless dongle to control")
            finally:
                selecting[0] = False

        def connection_changed():
            connection_pending[0] = False
            if closing[0]:
                return GLib.SOURCE_REMOVE
            writable[0] = False
            group.set_sensitive(False)
            last_backup[0] = None
            updates.pending = None
            if video_dialog[0]:
                video_dialog[0].destroy()
                video_dialog[0] = None
            if active_session[0]:
                active_session[0].request_stop()
            preview.queue_draw()
            update_connection_controls(rebuild=rebuild_pending[0])
            rebuild_pending[0] = False
            perform(read_state, True, "Current settings read.")
            return GLib.SOURCE_REMOVE

        def queue_connection_change(rebuild=False):
            writable[0] = False
            rebuild_pending[0] |= rebuild
            # GTK uses selection objects after notify::selected returns
            if not connection_pending[0]:
                connection_pending[0] = True
                GLib.idle_add(connection_changed)

        def choose_device(*_):
            if selecting[0] or busy[0]:
                return
            index = devices.get_selected()
            if index > len(connection.options):
                return
            connection.choose(connection.options[index - 1].path if index else None)
            queue_connection_change()

        def choose_model(*_):
            if selecting[0] or busy[0] or not connection.wireless:
                return
            connection.choose_model('f87' if paired_model.get_selected() == 1 else None)
            queue_connection_change()

        def scan_devices(*_):
            if busy[0]:
                return
            connection.refresh(device_options())
            queue_connection_change(rebuild=True)

        def selection_changed(*_):
            index = effect.get_selected()
            if index >= len(effect_names):
                return
            selected = effect_names[index]
            is_live = selected in LIVE_LABELS
            is_projection = selected in PROJECTIONS
            video_row.set_visible(selected == "video")
            screen_row.set_visible(selected == "screen")
            vivid_row.set_visible(is_projection)
            preview_label.set_visible(is_live)
            preview.set_visible(is_live)
            row.set_sensitive(selected == "solid" or is_live and not is_projection)
            row.set_subtitle("Colors come from the source" if is_projection else
                             "Starting color of the animation" if is_live and selected != "wireless-solid"
                             else "Used by solid color")
            bright_row.set_sensitive(selected != "off")
            speed_row.set_sensitive(selected in ANIMATED_EFFECTS or
                                    is_live and not is_projection and selected != "wireless-solid")
            speed_row.set_subtitle("Video plays at its original speed" if selected == "video" else
                                   "Wireless keyboard targets 4 FPS" if selected == "screen" and wireless[0] else
                                   "Smooth capture targets 25 FPS" if selected == "screen" else
                                   "Used by animated effects")
            effect.set_subtitle(LIVE_HINTS[selected] if is_live
                                else "Press keys to see this effect" if selected in {"reactive", "ripple"}
                                else "Built-in multicolor animation" if selected in ANIMATED_EFFECTS
                                else "")
            note.set_text("Keep this window open for live animation. Closing it restores your previous lighting."
                          if is_live else
                          "Settings stay on the keyboard. Each change saves a local backup.")

        effect.connect("notify::selected", selection_changed)
        selection_changed()

        def finish(future, sync_controls, success, live):
            if closing[0]:
                return GLib.SOURCE_REMOVE
            busy[0] = False
            devices.set_sensitive(True)
            rescan.set_sensitive(True)
            paired_model.set_sensitive(True)
            group.set_sensitive(writable[0])
            try:
                result = future.result()
                if result.get("selection_required"):
                    writable[0] = False
                    group.set_sensitive(False)
                    description.set_text("Choose a keyboard in the top bar.")
                    status.remove_css_class("error")
                    status.set_text("Choose which connection to control in the top bar."
                                    if connection.options else
                                    "Connect a keyboard or dongle, then refresh the device list in the top bar.")
                    return GLib.SOURCE_REMOVE
                writable[0] = result.get("writable", False)
                wireless[0] = result.get("transport") == "wireless-2.4g"
                group.set_sensitive(writable[0])
                description.set_text(result.get("model") or "Keyboard lighting")
                if not writable[0]:
                    message = ("Choose AULA F87 in Paired keyboard if that is your keyboard."
                               if wireless[0] else result.get('note', 'Read-only device'))
                    status.set_text(f"{result['model']}\n{message}\n{result['device']}")
                    refresh.set_sensitive(True)
                    restore.set_sensitive(False)
                    return GLib.SOURCE_REMOVE
                if "backup" in result:
                    last_backup[0] = result["backup"]
                labels = panel_effects(result.get("transport"))
                if effect_names != list(labels):
                    syncing[0] = True
                    effect_names[:] = labels
                    effect.set_model(Gtk.StringList.new(list(labels.values())))
                if sync_controls:
                    syncing[0] = True
                    known = {name: index for index, name in enumerate(effect_names)}
                    if result["effect"] in known:
                        effect.set_selected(known[result["effect"]])
                    if result.get("color"):
                        color.set_text(result["color"])
                    brightness.set_value(result.get("brightness", 3))
                    speed.set_value(result.get("speed", 2))
                    vividness.set_value(result.get("vividness", 100))
                selection_changed()
                status.remove_css_class("error")
                if result.get("streaming"):
                    if result["effect"] in PROJECTIONS:
                        status.set_text(projection_status(result))
                    else:
                        reactions = (LIVE_HINTS[result["effect"]]
                                 if result["typing_reactions"] or result["effect"] == "wireless-solid" else
                                 "Animation is running. Keypress access is unavailable.")
                        status.set_text(f"{LIVE_LABELS[result['effect']]} is live.\n{reactions}\nTyping is never saved.")
                else:
                    status.set_text(f"{success}\n{result['model']} · {result['effect']}\n{result['device']}")
            except Exception as error:
                status.add_css_class("error")
                status.set_text(str(error))
            finally:
                syncing[0] = False
                if live:
                    updates.complete()
                refresh.set_sensitive(not busy[0] and connection.device is not None)
                restore.set_sensitive(writable[0] and last_backup[0] is not None and not busy[0])
            return GLib.SOURCE_REMOVE

        def perform(operation, sync_controls=False, success="Settings verified.", live=False):
            if busy[0]:
                return
            busy[0] = True
            devices.set_sensitive(False)
            rescan.set_sensitive(False)
            paired_model.set_sensitive(False)
            group.set_sensitive(live)
            refresh.set_sensitive(False)
            restore.set_sensitive(False)
            status.remove_css_class("error")
            status.set_text("Starting capture… Select a display if the system picker opens."
                            if effect_names[effect.get_selected()] == "screen" else
                            "Talking to the keyboard…")

            future = executor.submit(operation)
            future.add_done_callback(lambda completed: GLib.idle_add(
                finish, completed, sync_controls, success, live))

        def send_settings(settings):
            def work():
                if settings[0] in LIVE_LABELS:
                    session = active_session[0]
                    if (session and session.running and session.effect == settings[0]
                            and not session.stopped.is_set()
                            and session.source_name == settings[4]):
                        session.update(*settings[1:4], vividness=settings[5])
                        return session.state()
                    stop_stream()
                    session = LiveSession(*settings[1:4], effect=settings[0], source=settings[4],
                                          vividness=settings[5], device=connection.device, model=connection.model)
                    active_session[0] = session
                    return session.start()
                return call_keyboard(lambda keyboard: keyboard.apply(*settings[:4]))

            perform(work, live=True)

        updates = LatestChangeQueue(send_settings)

        def apply_changed(*_):
            if syncing[0] or not writable[0]:
                return
            index = effect.get_selected()
            if index >= len(effect_names):
                return
            selected = effect_names[index]
            session = active_session[0]
            if session and session.effect != selected:
                session.request_stop()
            if selected == "video" and not video_path[0]:
                status.set_text("Choose a local video file to start projection.")
                return
            value = color.get_text() if selected == "solid" or selected in LIVE_LABELS and selected not in PROJECTIONS else "00c8ff"
            try:
                parse_color(value)
            except ValueError as error:
                status.add_css_class("error")
                status.set_text(str(error))
                return
            level, rate = brightness.get_value_as_int(), speed.get_value_as_int()
            source = (video_path[0] if selected == "video" else
                      outputs[screen_row.get_selected()] if selected == "screen" else None)
            session = active_session[0]
            if session and (session.effect != selected or session.source_name != source):
                session.request_stop()
            updates.submit((selected, value, level, rate, source, round(vividness.get_value())))

        def choose_video(*_):
            chooser = Gtk.FileChooserNative.new("Choose a video", window,
                Gtk.FileChooserAction.OPEN, "Open", "Cancel")
            video_dialog[0] = chooser
            videos = Gtk.FileFilter()
            videos.set_name("Video files")
            videos.add_mime_type("video/*")
            chooser.add_filter(videos)
            files = Gtk.FileFilter()
            files.set_name("All files")
            files.add_pattern("*")
            chooser.add_filter(files)

            def chosen(dialog, response):
                if response == Gtk.ResponseType.ACCEPT and not closing[0]:
                    path = dialog.get_file().get_path()
                    if path:
                        video_path[0] = path
                        video_row.set_subtitle(path)
                        apply_changed()
                dialog.destroy()
                video_dialog[0] = None

            chooser.connect("response", chosen)
            chooser.show()

        video_button.connect("clicked", choose_video)
        screen_row.connect("notify::selected", apply_changed)

        effect.connect("notify::selected", apply_changed)
        color.connect("changed", apply_changed)
        brightness.connect("value-changed", apply_changed)
        vividness.connect("value-changed", apply_changed)
        speed.connect("value-changed", apply_changed)
        refresh.connect("clicked", lambda *_: perform(read_state, True, "Current settings read."))

        def restore_clicked(*_):
            backup = last_backup[0]
            perform(lambda: call_keyboard(lambda keyboard: keyboard.restore(backup)),
                    True, "Previous settings restored.")

        restore.connect("clicked", restore_clicked)
        devices.connect("notify::selected", choose_device)
        paired_model.connect("notify::selected", choose_model)
        rescan.connect("clicked", scan_devices)

        def close_requested(*_):
            closing[0] = True
            if video_dialog[0]:
                video_dialog[0].destroy()
                video_dialog[0] = None
            session = active_session[0]
            if session:
                session.request_stop()
            return False

        def monitor_stream():
            if closing[0]:
                return GLib.SOURCE_REMOVE
            session = active_session[0]
            if session and session.error and not busy[0]:
                status.add_css_class("error")
                status.set_text(session.error)
            elif session and session.running and session.effect in PROJECTIONS and not busy[0]:
                try:
                    status.set_text(projection_status(session.state()))
                except Exception as error:
                    status.add_css_class("error")
                    status.set_text(str(error))
            return GLib.SOURCE_CONTINUE

        window.connect("close-request", close_requested)
        GLib.timeout_add(500, monitor_stream)

        def update_preview():
            if closing[0]:
                return GLib.SOURCE_REMOVE
            if preview.get_visible() and active_session[0]:
                preview.queue_draw()
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(40, update_preview)
        window.present()
        scan_devices()

    app.connect("activate", activate)
    try:
        return app.run([])
    finally:
        executor.shutdown(wait=True)
        stop_stream()
