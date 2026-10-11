"""Native controls using the GTK4 and libadwaita already installed on this machine."""

from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from aula import ANIMATED_EFFECTS, EFFECT_LABELS, Keyboard, parse_color
from aula_live import LatestChangeQueue
from aula_aurora import LIVE_HINTS, LIVE_LABELS, LAYOUT, LiveSession


PROJECTIONS = {"video", "screen"}


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


def run(device=None):
    app = Adw.Application(application_id="local.chamakchhallo.Lighting")
    executor = ThreadPoolExecutor(max_workers=1)
    active_session = [None]

    def stop_stream():
        session, active_session[0] = active_session[0], None
        if session:
            session.stop()

    def call_keyboard(operation):
        stop_stream()
        with Keyboard(device) as keyboard:
            return operation(keyboard)

    def read_state():
        session = active_session[0]
        if session and session.running:
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
        outer.append(Adw.HeaderBar())
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
        group = Adw.PreferencesGroup()
        page.append(group)
        labels = {**EFFECT_LABELS, **LIVE_LABELS}
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

        def selection_changed(*_):
            selected = effect_names[effect.get_selected()]
            video_row.set_visible(selected == "video")
            screen_row.set_visible(selected == "screen")
            vivid_row.set_visible(selected in PROJECTIONS)
            preview_label.set_visible(selected in LIVE_LABELS)
            preview.set_visible(selected in LIVE_LABELS)
            row.set_sensitive(selected == "solid" or selected in LIVE_LABELS and selected not in PROJECTIONS)
            row.set_subtitle("Colors come from the source" if selected in PROJECTIONS else
                             "Starting color of the animation" if selected in LIVE_LABELS
                             else "Used by solid color")
            bright_row.set_sensitive(selected != "off")
            speed_row.set_sensitive(selected in ANIMATED_EFFECTS or
                                    selected in LIVE_LABELS and selected not in PROJECTIONS)
            speed_row.set_subtitle("Video plays at its original speed" if selected == "video" else
                                   "Smooth capture targets 25 FPS" if selected == "screen" else
                                   "Used by animated effects")
            effect.set_subtitle(LIVE_HINTS[selected] if selected in LIVE_LABELS
                                else "Press keys to see this effect" if selected in {"reactive", "ripple"}
                                else "Built-in multicolor animation" if selected in ANIMATED_EFFECTS
                                else "")
            note.set_text("Keep this window open for live animation. Closing it restores your previous lighting."
                          if selected in LIVE_LABELS else
                          "Settings stay on the keyboard. Each change saves a local backup.")

        effect.connect("notify::selected", selection_changed)
        selection_changed()

        def finish(future, sync_controls, success, live):
            if closing[0]:
                return GLib.SOURCE_REMOVE
            busy[0] = False
            group.set_sensitive(writable[0])
            try:
                result = future.result()
                writable[0] = result.get("writable", False)
                group.set_sensitive(writable[0])
                description.set_text(result.get("model") or "Keyboard lighting")
                if not writable[0]:
                    status.set_text(f"{result['model']}\n{result.get('note', 'Read-only device')}\n{result['device']}")
                    refresh.set_sensitive(True)
                    restore.set_sensitive(False)
                    return GLib.SOURCE_REMOVE
                if "backup" in result:
                    last_backup[0] = result["backup"]
                if sync_controls:
                    syncing[0] = True
                    known = {name: index for index, name in enumerate(effect_names)}
                    if result["effect"] in known:
                        effect.set_selected(known[result["effect"]])
                    color.set_text(result["color"])
                    brightness.set_value(result.get("brightness", 3))
                    speed.set_value(result.get("speed", 2))
                    vividness.set_value(result.get("vividness", 100))
                status.remove_css_class("error")
                if result.get("streaming"):
                    if result["effect"] in PROJECTIONS:
                        status.set_text(projection_status(result))
                    else:
                        reactions = (LIVE_HINTS[result["effect"]]
                                 if result["typing_reactions"] else
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
            refresh.set_sensitive(not busy[0])
            restore.set_sensitive(last_backup[0] is not None and not busy[0])
            return GLib.SOURCE_REMOVE

        def perform(operation, sync_controls=False, success="Settings verified.", live=False):
            if busy[0]:
                return
            busy[0] = True
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
                                          vividness=settings[5], device=device)
                    active_session[0] = session
                    return session.start()
                return call_keyboard(lambda keyboard: keyboard.apply(*settings[:4]))

            perform(work, live=True)

        updates = LatestChangeQueue(send_settings)

        def apply_changed(*_):
            if syncing[0] or not writable[0]:
                return
            selected = effect_names[effect.get_selected()]
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
        perform(read_state, True, "Current settings read.")

    app.connect("activate", activate)
    try:
        return app.run([])
    finally:
        executor.shutdown(wait=True)
        stop_stream()
