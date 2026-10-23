# -*- coding: utf-8 -*-
"""
PepeMonitor — настройки любых экранов компьютера в одном окне:
  * внешние мониторы по DDC/CI — яркость, контраст, резкость, цветовой режим, R/G/B внутри монитора;
  * экран ноутбука — яркость подсветки;
  * любой физический экран — программно: теплота, гамма, затемнение (через видеокарту).

Запуск:  python pepe_monitor.py
         python pepe_monitor.py --profile "Ночной"   — применить профиль без окна (для ярлыков)
         python pepe_monitor.py --restore            — вернуть программную коррекцию (автозапуск при входе)
"""

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
from tkinter import messagebox, simpledialog, ttk

import ddc
import displays
import power

APP_NAME = "PepeMonitor"
VERSION = "1.3"

BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
EXE_PATH = sys.executable if getattr(sys, "frozen", False) else None
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup",
                           APP_NAME + ".lnk")

# same palette as PepeDisplay
BG = "#E3F2D9"
CARD = "#F4FAEF"
GREEN = "#58A032"
GREEN_DARK = "#2F6B1B"
TEXT = "#283C1E"
MUTED = "#7A8C72"
ERROR = "#B03A2E"

HW_SLIDERS = [("brightness", "Яркость"), ("contrast", "Контраст"), ("sharpness", "Резкость")]
GAIN_SLIDERS = [("red", "Красный", "#C0392B"), ("green", "Зелёный", "#3C9A2E"), ("blue", "Синий", "#2E6FC0")]
# field, label, from, to, step, unit
SOFT_SLIDERS = [("temp", "Теплота", 2700, 6500, 100, "K"),
                ("gamma", "Гамма", 50, 150, 1, "%"),
                ("dim", "Затемнение", 20, 100, 1, "%")]
OFF_TIMER_MINUTES = [5, 10, 15, 30, 45, 60, 90, 120]
TIMEOUT_MINUTES = [0, 1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 120, 180, 300]


def minutes_text(m):
    if not m:
        return "никогда"
    return "%d ч" % (m // 60) if m >= 60 and m % 60 == 0 else "%d мин" % m

DEFAULT_PROFILES = {
    "Дневной": {"displays": {"*": {"hw": {"preset": 0x05, "brightness": 80},
                                   "soft": dict(displays.NEUTRAL_SOFT)}}},
    "Ночной": {"displays": {"*": {"hw": {"preset": 0x05, "brightness": 30},
                                  "soft": {"temp": 4500, "gamma": 100, "dim": 100}}}},
}

# 1.0 defaults, replaced by the new ones if the user never changed them
OLD_DEFAULT_PROFILES = {
    "Дневной": {"preset": 0x05, "brightness": 75, "contrast": 50},
    "Ночной": {"preset": 0x04, "brightness": 25, "contrast": 50},
}


# ---------- settings & profiles ----------
def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("profiles", json.loads(json.dumps(DEFAULT_PROFILES)))
    data.setdefault("last_preset", {})
    data.setdefault("soft", {})
    for name, p in data["profiles"].items():
        if "displays" not in p:  # 1.0 profiles: flat DDC values for any monitor
            untouched = OLD_DEFAULT_PROFILES.get(name) == p
            data["profiles"][name] = json.loads(json.dumps(DEFAULT_PROFILES[name])) if untouched                 else {"displays": {"*": {"hw": p}}}
    return data


def save_settings(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


def apply_profile(display, profile, settings):
    """Applies the profile entry for this screen (or the '*' one). Returns False if something was refused."""
    entry = profile["displays"].get(display.key) or profile["displays"].get("*")
    if not entry:
        return True
    ok = True
    hw = entry.get("hw", {})
    # preset first: switching it makes the monitor reload its R/G/B gains
    for field in ["preset"] + [f for f in hw if f != "preset"]:
        if field in hw and display.supports(field):
            ok &= display.set(field, hw[field])
            if field == "preset":
                settings["last_preset"][display.key] = hw[field]
    soft = entry.get("soft")
    if soft and display.soft_capable:
        soft = dict(displays.NEUTRAL_SOFT, **soft)
        ok &= display.set_soft(soft["temp"], soft["gamma"], soft["dim"])
        settings["soft"][display.key] = soft
    return ok


def restore_soft(display, settings):
    soft = settings["soft"].get(display.key)
    if soft and display.soft_capable and soft != displays.NEUTRAL_SOFT:
        return display.set_soft(soft["temp"], soft["gamma"], soft["dim"])
    return True


def run_cli(args):
    settings = load_settings()
    profile = None
    if "--profile" in args:
        i = args.index("--profile")
        profile = settings["profiles"].get(args[i + 1] if i + 1 < len(args) else "")
        if profile is None:
            return 2
    found = displays.enumerate_displays()
    try:
        for d in found:
            if profile:
                apply_profile(d, profile, settings)
            else:
                restore_soft(d, settings)
        if profile:
            save_settings(settings)
    finally:
        displays.release(found)
    return 0 if found else 1


def desktop_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
    except OSError:
        return os.path.join(os.path.expanduser("~"), "Desktop")


def make_shortcut(path, args, description=""):
    """Windows .lnk to this program; values go through env vars so quotes in names can't break the script."""
    if EXE_PATH:
        target, workdir = EXE_PATH, os.path.dirname(EXE_PATH)
    else:  # running from source
        target = sys.executable.replace("python.exe", "pythonw.exe")
        args = '"%s" %s' % (os.path.abspath(__file__), args)
        workdir = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, PM_LNK=path, PM_TARGET=target, PM_ARGS=args, PM_DIR=workdir,
               PM_ICON=os.path.join(BASE_DIR, "frog.ico"), PM_DESC=description)
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:PM_LNK);"
              "$s.TargetPath=$env:PM_TARGET;$s.Arguments=$env:PM_ARGS;$s.WorkingDirectory=$env:PM_DIR;"
              "$s.Description=$env:PM_DESC;if(Test-Path $env:PM_ICON){$s.IconLocation=$env:PM_ICON};$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0


# ---------- background DDC / WMI ----------
class Worker(threading.Thread):
    """DDC/CI (~50 ms) and WMI (~300 ms) calls must not block the UI.
    Slider moves overwrite each other, so only the latest value per control is sent."""

    def __init__(self, post):
        super().__init__(daemon=True)
        self.post = post
        self.jobs = queue.Queue()
        self.pending = {}
        self.lock = threading.Lock()

    def latest(self, key, fn):
        """Queue fn(); if the same key is still waiting, the newer fn replaces it."""
        with self.lock:
            fresh = key not in self.pending
            self.pending[key] = fn
        if fresh:
            self.jobs.put(("latest", key, None))

    def call(self, fn, done=None):
        self.jobs.put(("call", fn, done))

    def run(self):
        while True:
            kind, fn, done = self.jobs.get()
            if kind == "latest":
                with self.lock:
                    fn = self.pending.pop(fn)
            try:
                result = fn()
            except Exception as e:  # report instead of killing the worker
                result = e
            if done:
                self.post(lambda r=result, done=done: done(r))  # bind now: the loop rebinds done
            elif result is False or isinstance(result, Exception):
                self.post(lambda r=result: App.instance.command_failed(r))


# ---------- window ----------
class App:
    instance = None

    def __init__(self, root):
        App.instance = self
        self.root = root
        self.settings = load_settings()
        self.displays = []
        self.display = None
        self.values = {}  # display key -> {field: value} last read or set
        self.ui_queue = queue.Queue()
        self.loading = False
        self.vars = {}
        self.rows = {}
        self.preset_buttons = {}
        self.wide_asked = False
        self.save_job = None
        self.off_at = None  # time.monotonic() when the off timer fires
        self.off_job = None
        self.timeout_vars = {}

        root.title(APP_NAME)
        root.configure(bg=BG)
        # near the top: the window is tall and the default cascade position pushes its bottom under the taskbar
        root.geometry("+%d+%d" % (max(0, (root.winfo_screenwidth() - 1060) // 2), 10))
        icon = os.path.join(BASE_DIR, "frog.ico")
        if os.path.exists(icon):
            root.iconbitmap(icon)

        self.worker = Worker(self.ui_queue.put)
        self.worker.start()
        self._style()
        self._build()
        root.after(30, self._drain)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.worker.call(lambda: {k: power.get_timeout(k) for k in power.TIMEOUTS}, self._on_timeouts)

    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        s.configure("Card.TFrame", background=CARD)
        s.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("CardTitle.TLabel", background=CARD, foreground=GREEN_DARK, font=("Segoe UI Semibold", 11))
        s.configure("Value.TLabel", background=CARD, foreground=GREEN_DARK, font=("Segoe UI Semibold", 10))
        s.configure("Status.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Horizontal.TScale", background=CARD, troughcolor="#D3E6C6", bordercolor=CARD,
                    lightcolor=GREEN, darkcolor=GREEN_DARK)
        s.configure("TCombobox", fieldbackground="white", background=CARD, arrowcolor=GREEN_DARK)
        s.configure("TButton", background=CARD, foreground=GREEN_DARK, bordercolor=GREEN,
                    focuscolor=CARD, padding=(10, 4))
        s.map("TButton", background=[("active", "#D3E6C6"), ("disabled", CARD)],
              foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", background=GREEN, foreground="white", bordercolor=GREEN_DARK)
        s.map("Accent.TButton", background=[("active", GREEN_DARK)])
        s.configure("Card.TCheckbutton", background=CARD, foreground=TEXT)
        s.map("Card.TCheckbutton", background=[("active", CARD)])

    def _card(self, column, title):
        card = ttk.Frame(column, style="Card.TFrame", padding=(14, 8))
        card.grid(row=len(column.grid_slaves()), column=0, sticky="ew", pady=(0, 8))
        column.columnconfigure(0, weight=1)
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, 4))
        return card

    def _slider_group(self, card):
        group = tk.Frame(card, bg=CARD)
        group.pack(fill="x")
        group.columnconfigure(0, weight=1)
        return group

    def _build(self):
        header = tk.Frame(self.root, bg=GREEN_DARK)
        header.pack(fill="x")
        logo = os.path.join(BASE_DIR, "frog.png")
        if os.path.exists(logo):
            img = tk.PhotoImage(file=logo)
            self.logo = img.subsample(max(1, img.width() // 44))
            tk.Label(header, image=self.logo, bg=GREEN_DARK).pack(side="left", padx=(12, 6), pady=8)
        tk.Label(header, text=APP_NAME, bg=GREEN_DARK, fg="white",
                 font=("Segoe UI Semibold", 16)).pack(side="left")
        tk.Label(header, text="настройки экранов", bg=GREEN_DARK, fg="#BFE0A8",
                 font=("Segoe UI", 10)).pack(side="left", padx=8, pady=(6, 0))

        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=14, pady=8)
        self.display_var = tk.StringVar()
        self.display_combo = ttk.Combobox(top, textvariable=self.display_var, state="readonly")
        self.display_combo.pack(side="left", fill="x", expand=True)
        self.display_combo.bind("<<ComboboxSelected>>", lambda e: self.select(self.display_combo.current()))
        ttk.Button(top, text="⟳", width=3, command=self.refresh).pack(side="left", padx=(6, 0))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14)
        left, right = tk.Frame(body, bg=BG), tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="new", padx=(0, 4))
        right.grid(row=0, column=1, sticky="new", padx=(4, 0))
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")

        # left: what the monitor itself can do
        self.card_hw = self._card(left, "Изображение")
        self.hw_group = self._slider_group(self.card_hw)
        for field, label in HW_SLIDERS:
            self._slider(self.hw_group, field, label, 0, 100, 1, "", self.on_hw)
        self.hw_note = ttk.Label(self.card_hw, style="Muted.TLabel", wraplength=440, justify="left")
        self.hw_note.pack(anchor="w")

        self.card_color = self._card(left, "Цвет монитора")
        self.preset_row = tk.Frame(self.card_color, bg=CARD)
        self.preset_row.pack(fill="x", pady=(0, 4))
        group = self._slider_group(self.card_color)
        for field, label, color in GAIN_SLIDERS:
            self._slider(group, field, label, 0, 100, 1, "", self.on_hw, color)
        ttk.Label(self.card_color, text="R/G/B работают в режиме «Свой» — он включится сам.",
                  style="Muted.TLabel").pack(anchor="w")
        ttk.Button(self.card_color, text="Сбросить цвет к заводскому",
                   command=self.reset_color).pack(anchor="w", pady=(6, 0))

        # right: software correction, profiles, power
        self.card_soft = self._card(right, "Цвет на экране (программно)")
        self.soft_group = self._slider_group(self.card_soft)
        for field, label, lo, hi, step, unit in SOFT_SLIDERS:
            self._slider(self.soft_group, field, label, lo, hi, step, unit, self.on_soft)
        self.soft_note = ttk.Label(self.card_soft, style="Muted.TLabel", wraplength=440, justify="left")
        self.soft_note.pack(anchor="w")
        row = tk.Frame(self.card_soft, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        self.soft_reset = ttk.Button(row, text="Сбросить", command=self.reset_soft)
        self.soft_reset.pack(side="left")
        self.autostart_var = tk.BooleanVar(value=os.path.exists(STARTUP_LNK))
        ttk.Checkbutton(row, text="Возвращать при входе в Windows", variable=self.autostart_var,
                        style="Card.TCheckbutton", command=self.toggle_autostart).pack(side="left", padx=10)

        card = self._card(right, "Профили (для всех экранов)")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        self.profile_list = tk.Listbox(row, height=4, activestyle="none", relief="flat", highlightthickness=1,
                                       highlightbackground="#C9DDBB", selectbackground=GREEN,
                                       font=("Segoe UI", 10), exportselection=False)
        self.profile_list.pack(side="left", fill="both", expand=True)
        self.profile_list.bind("<Double-Button-1>", lambda e: self.apply_selected_profile())
        buttons = tk.Frame(row, bg=CARD)
        buttons.pack(side="left", padx=(10, 0), fill="y")
        for text, cmd, style in [("Применить", self.apply_selected_profile, "Accent.TButton"),
                                 ("Сохранить текущие…", self.save_profile, "TButton"),
                                 ("Ярлык на стол", self.profile_shortcut, "TButton"),
                                 ("Удалить", self.delete_profile, "TButton")]:
            ttk.Button(buttons, text=text, command=cmd, style=style).pack(fill="x", pady=1)
        self._fill_profiles()

        card = self._card(right, "Питание")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        ttk.Button(row, text="Выключить все экраны", command=self.screens_off).pack(side="left")
        # shown only for a monitor that has DDC/CI power (D6) — see select()
        self.power_row = tk.Frame(row, bg=CARD)
        tk.Label(self.power_row, text="Монитор:", bg=CARD, fg=TEXT, font=("Segoe UI", 10)).pack(side="left", padx=(12, 0))
        for text, on in (("Выкл", False), ("Вкл", True)):
            ttk.Button(self.power_row, text=text, width=5,
                       command=lambda on=on: self.monitor_power(on)).pack(side="left", padx=(6, 0))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text="Выключить экраны через", bg=CARD, fg=TEXT, font=("Segoe UI", 10)).pack(side="left")
        self.off_var = tk.StringVar(value=minutes_text(30))
        ttk.Combobox(row, textvariable=self.off_var, values=[minutes_text(m) for m in OFF_TIMER_MINUTES],
                     width=8, state="readonly").pack(side="left", padx=6)
        self.off_button = ttk.Button(row, text="Запустить", width=16, command=self.toggle_off_timer)
        self.off_button.pack(side="left")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        for kind, label in (("display", "Windows: гасить экран"), ("sleep", "сон")):
            tk.Label(row, text=label, bg=CARD, fg=TEXT, font=("Segoe UI", 10)).pack(side="left")
            var = self.timeout_vars[kind] = tk.StringVar(value="…")
            combo = ttk.Combobox(row, textvariable=var, values=[minutes_text(m) for m in TIMEOUT_MINUTES],
                                 width=8, state="readonly")
            combo.pack(side="left", padx=(6, 12))
            combo.bind("<<ComboboxSelected>>", lambda e, k=kind: self.set_timeout(k))
        ttk.Label(card, text="Выключение без сигнала — в меню самого монитора.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        self.status_var = tk.StringVar()
        self.status_label = ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(fill="x", padx=14, pady=(0, 8))

    def _slider(self, group, field, label, lo, hi, step, unit, callback, color=None):
        row = tk.Frame(group, bg=CARD)
        row.grid(row=len(group.grid_slaves()), column=0, sticky="ew", pady=2)
        tk.Label(row, text=label, bg=CARD, fg=color or TEXT, width=10, anchor="w",
                 font=("Segoe UI", 10)).pack(side="left")
        var = tk.StringVar()
        scale = ttk.Scale(row, from_=lo, to=hi, orient="horizontal",
                          command=lambda v: self._moved(field, step, unit, v, callback))
        scale.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(row, textvariable=var, style="Value.TLabel", width=6, anchor="e").pack(side="left")
        self.vars[field] = var
        self.rows[field] = (row, scale, unit)

    def _moved(self, field, step, unit, value, callback):
        value = int(round(float(value) / step) * step)
        self.vars[field].set("%d%s" % (value, unit))
        if not self.loading and self.display:
            callback(field, value)

    def _set_slider(self, field, value, maximum=None):
        row, scale, unit = self.rows[field]
        if maximum:
            scale.configure(to=maximum)
        scale.set(value)
        self.vars[field].set("%d%s" % (value, unit))

    def _show(self, field, visible):
        # grid_remove keeps the row's place, so it comes back in the same spot
        row = self.rows[field][0]
        row.grid() if visible else row.grid_remove()

    @staticmethod
    def _show_group(group, before, visible):
        # Tk keeps the old size of a frame whose last child is hidden, so hide the whole group
        if visible and not group.winfo_manager():
            group.pack(fill="x", before=before)
        elif not visible:
            group.pack_forget()

    # ---------- plumbing ----------
    def _drain(self):
        self.root.after(30, self._drain)  # first, so one failing callback can't stop the queue
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass

    def status(self, text, error=False):
        self.status_var.set(text)
        self.status_label.configure(foreground=ERROR if error else MUTED)

    def command_failed(self, result):
        if isinstance(result, Exception):
            self.status("Ошибка: %s" % result, error=True)
        else:
            self.status("Экран не принял команду", error=True)

    def close(self):
        if self.off_at and not messagebox.askyesno(APP_NAME, "Таймер выключения экранов отменится. Закрыть?"):
            return
        displays.release(self.displays)
        self.root.destroy()

    # ---------- screens ----------
    def refresh(self):
        self.status("Ищу экраны…")
        old, self.displays, self.display = self.displays, [], None

        def find():
            displays.release(old)
            found = displays.enumerate_displays()
            for d in found:  # gamma ramps are lost after sleep / reconnect
                restore_soft(d, self.settings)
            return found

        self.worker.call(find, self._on_displays)

    def _on_displays(self, found):
        if isinstance(found, Exception):
            self.status("Ошибка: %s" % found, error=True)
            return
        self.displays = found
        self.display_combo["values"] = [d.title for d in found]
        if not found:
            self.display_var.set("")
            self.status("Экраны не найдены", error=True)
            return
        # a virtual screen (tablet) has nothing to adjust, so don't open on it
        first = next((i for i, d in enumerate(found) if not d.virtual), 0)
        self.display_combo.current(first)
        self.select(first)

    def select(self, index):
        d = self.display = self.displays[index]
        self.root.title("%s — %s" % (APP_NAME, d.name))

        for field, _ in HW_SLIDERS:
            self._show(field, d.supports(field))
        self._show_group(self.hw_group, self.hw_note, any(d.supports(f) for f, _ in HW_SLIDERS))
        if d.virtual:
            note = "Виртуальный экран: яркость меняй на самом устройстве (планшете)."
        elif not any(d.supports(f) for f, _ in HW_SLIDERS):
            note = "Монитор не отвечает по DDC/CI — включи этот пункт в его меню. Пока можно затемнять программно →"
        elif d.internal:
            note = "Яркость подсветки, как клавишами Fn."
        else:
            note = ""
        self.hw_note.configure(text=note)

        has_color = d.ddc is not None and any(d.supports(f) for f in ("preset", "red", "green", "blue"))
        if has_color:
            self.card_color.grid()
            for field, *_ in GAIN_SLIDERS:
                self._show(field, d.supports(field))
            self._build_presets(d)
        else:
            self.card_color.grid_remove()
        if d.supports("power"):
            self.power_row.pack(side="left")
        else:
            self.power_row.pack_forget()

        self._show_group(self.soft_group, self.soft_note, d.soft_capable)
        self.soft_reset.state(["!disabled"] if d.soft_capable else ["disabled"])
        self.soft_note.configure(text="Через видеокарту, работает на любом физическом экране."
                                 if d.soft_capable else
                                 "Для виртуальных экранов не работает: картинка уходит на устройство раньше, "
                                 "чем видеокарта её корректирует.")
        soft = self.current_soft(d)
        self.loading = True
        for field, *_ in SOFT_SLIDERS:
            self._set_slider(field, soft[field])
        self.loading = False

        fields = [f for f in ("brightness", "contrast", "sharpness", "red", "green", "blue") if d.supports(f)]
        if not fields:
            self.status("Готово")
            return
        self.status("Читаю настройки…")
        self.worker.call(lambda: {f: d.get(f) for f in fields}, lambda v: self._on_values(d, v))

    def _on_values(self, d, values):
        if d is not self.display:
            return
        if isinstance(values, Exception):
            self.status("Ошибка: %s" % values, error=True)
            return
        self.loading = True
        cache = self.values.setdefault(d.key, {})
        for field, reply in values.items():
            if reply is not None:
                self._set_slider(field, reply[0], reply[1] or 100)
                cache[field] = reply[0]
        self.loading = False
        self._mark_preset(self.settings["last_preset"].get(d.key))
        self.status("Готово")

    def _build_presets(self, d):
        for w in self.preset_row.winfo_children():
            w.destroy()
        self.preset_buttons = {}
        for i, code in enumerate(d.ddc.caps.get(ddc.COLOR_PRESET) or []):
            b = tk.Button(self.preset_row, text=ddc.PRESET_NAMES.get(code, "Режим %d" % code),
                          relief="flat", bd=0, padx=6, pady=4, cursor="hand2", font=("Segoe UI", 9),
                          command=lambda c=code: self.set_preset(c))
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=2, pady=2)
            self.preset_row.columnconfigure(i % 3, weight=1)
            self.preset_buttons[code] = b
        self._mark_preset(None)

    def _mark_preset(self, current):
        # the monitor's own read-back of VCP 0x14 is often wrong, so the last one set here is shown
        for code, b in self.preset_buttons.items():
            on = code == current
            b.configure(bg=GREEN if on else "#DCEBD0", fg="white" if on else GREEN_DARK,
                        activebackground=GREEN_DARK, activeforeground="white")

    def reselect(self, d, delay):
        self.root.after(delay, lambda: self.select(self.displays.index(d)) if d is self.display else None)

    # ---------- monitor controls ----------
    def on_hw(self, field, value):
        d = self.display
        if field in ("red", "green", "blue") and ddc.USER_PRESET in self.preset_buttons \
                and self.settings["last_preset"].get(d.key) != ddc.USER_PRESET:
            self.set_preset(ddc.USER_PRESET, reread=False)
        self.values.setdefault(d.key, {})[field] = value
        self.worker.latest((d.key, field), lambda: d.set(field, value))

    def set_preset(self, code, reread=True):
        d = self.display
        self.worker.latest((d.key, "preset"), lambda: d.set("preset", code))
        self.settings["last_preset"][d.key] = code
        save_settings(self.settings)
        self._mark_preset(code)
        self.status("Режим: %s" % ddc.PRESET_NAMES.get(code, code))
        if reread:  # gains change with the preset
            self.reselect(d, 600)

    def reset_color(self):
        d = self.display
        if messagebox.askyesno(APP_NAME, "Вернуть заводские настройки цвета монитора?"):
            self.worker.call(lambda: d.ddc.set(ddc.COLOR_RESET, 1), lambda ok: self.reselect(d, 800))

    # ---------- power ----------
    def screens_off(self):
        self.status("Экраны выключены — подвинь мышь или нажми клавишу, чтобы включить")
        # wait for the click to finish: mouse movement right after the command wakes the screens again
        self.root.after(500, power.screens_off)

    def monitor_power(self, on):
        d = self.display

        def done(ok):
            if ok is True:
                self.status("Монитор включён" if on else "Монитор выключен — включить: «Вкл» или кнопка на мониторе")
            else:
                self.status("Монитор не ответил — нажми кнопку питания на нём", error=True)

        self.worker.call(lambda: d.set("power", ddc.POWER_ON if on else ddc.POWER_OFF), done)

    def toggle_off_timer(self):
        if self.off_at:
            self.root.after_cancel(self.off_job)
            self.off_at = self.off_job = None
            self.off_button.configure(text="Запустить")
            self.status("Таймер отменён")
            return
        minutes = next(m for m in OFF_TIMER_MINUTES if minutes_text(m) == self.off_var.get())
        self.off_at = time.monotonic() + minutes * 60
        self._off_tick()
        self.status("Экраны погаснут по таймеру — окно не закрывай")

    def _off_tick(self):
        left = int(self.off_at - time.monotonic())
        if left <= 0:
            self.off_at = self.off_job = None
            self.off_button.configure(text="Запустить")
            power.screens_off()
            return
        self.off_button.configure(text="Отменить (%d:%02d)" % divmod(left, 60))
        self.off_job = self.root.after(1000, self._off_tick)

    def _on_timeouts(self, values):
        if isinstance(values, Exception):
            values = {}
        for kind, var in self.timeout_vars.items():
            m = values.get(kind)
            var.set("?" if m is None else minutes_text(m))

    def set_timeout(self, kind):
        text = self.timeout_vars[kind].get()
        minutes = next(m for m in TIMEOUT_MINUTES if minutes_text(m) == text)

        def done(ok):
            if ok is True:
                self.status("Настройки электропитания Windows изменены")
            else:
                self.status("Windows не дала изменить электропитание", error=True)
                self.worker.call(lambda: {k: power.get_timeout(k) for k in power.TIMEOUTS}, self._on_timeouts)

        self.worker.call(lambda: power.set_timeout(kind, minutes), done)

    # ---------- software colour ----------
    def current_soft(self, d):
        return dict(displays.NEUTRAL_SOFT, **self.settings["soft"].get(d.key, {}))

    def on_soft(self, field, value):
        d = self.display
        soft = self.current_soft(d)
        soft[field] = value
        self.settings["soft"][d.key] = soft
        self.worker.latest((d.key, "soft"), lambda: d.set_soft(soft["temp"], soft["gamma"], soft["dim"]))
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(500, lambda: self._soft_settled(d, soft))

    def _soft_settled(self, d, soft):
        self.save_job = None
        save_settings(self.settings)
        # Windows refuses strong ramps; check the final value once the slider stops
        self.worker.call(lambda: d.set_soft(soft["temp"], soft["gamma"], soft["dim"]), self._soft_checked)

    def _soft_checked(self, ok):
        if ok is True:
            self.status("Готово")
            return
        if displays.wide_gamma_range_enabled():
            self.status("Видеокарта не приняла такую сильную коррекцию", error=True)
            return
        self.status("Windows не даёт так сильно менять цвет", error=True)
        if not self.wide_asked:
            self.wide_asked = True
            if messagebox.askyesno(APP_NAME, "Windows по умолчанию не разрешает сильное затемнение и тёплые "
                                             "ночные цвета.\n\nСнять ограничение? Понадобятся права "
                                             "администратора (один раз), потом выйти из Windows и войти."):
                self.worker.call(displays.enable_wide_gamma_range, self._wide_done)

    def _wide_done(self, ok):
        if ok is True:
            messagebox.showinfo(APP_NAME, "Готово. Выйди из Windows и войди снова — тогда ограничение снимется.")
        else:
            self.status("Не удалось снять ограничение", error=True)

    def reset_soft(self):
        d = self.display
        self.settings["soft"].pop(d.key, None)
        save_settings(self.settings)
        self.loading = True
        for field, *_ in SOFT_SLIDERS:
            self._set_slider(field, displays.NEUTRAL_SOFT[field])
        self.loading = False
        n = displays.NEUTRAL_SOFT
        self.worker.latest((d.key, "soft"), lambda: d.set_soft(n["temp"], n["gamma"], n["dim"]))
        self.status("Программная коррекция выключена")

    def toggle_autostart(self):
        if self.autostart_var.get():
            if make_shortcut(STARTUP_LNK, "--restore", "PepeMonitor: вернуть цвет экранов"):
                self.status("Коррекция будет возвращаться при входе в Windows")
            else:
                self.autostart_var.set(False)
                self.status("Не удалось добавить в автозагрузку", error=True)
        else:
            try:
                os.remove(STARTUP_LNK)
            except OSError:
                pass
            self.status("Убрано из автозагрузки")

    # ---------- profiles ----------
    def _fill_profiles(self):
        self.profile_list.delete(0, "end")
        for name in self.settings["profiles"]:
            self.profile_list.insert("end", name)

    def _selected_profile(self):
        sel = self.profile_list.curselection()
        if not sel:
            self.status("Выбери профиль в списке", error=True)
            return None
        return list(self.settings["profiles"])[sel[0]]

    def apply_selected_profile(self):
        name = self._selected_profile()
        if not name:
            return
        profile = self.settings["profiles"][name]
        self.status("Применяю «%s»…" % name)

        def apply_all():
            ok = all([apply_profile(d, profile, self.settings) for d in self.displays])
            save_settings(self.settings)
            return ok

        def done(ok):
            self.status("Профиль «%s» применён" % name if ok is True else
                        "Профиль «%s» применён не полностью" % name, error=ok is not True)
            if self.display:
                self.reselect(self.display, 500)

        self.worker.call(apply_all, done)

    def save_profile(self):
        name = simpledialog.askstring(APP_NAME, "Название профиля (сохранятся все экраны):", parent=self.root)
        if not name:
            return
        entries = {}
        for d in self.displays:
            hw = {f: v for f, v in self.values.get(d.key, {}).items() if d.supports(f)}
            preset = self.settings["last_preset"].get(d.key)
            if preset is not None and d.supports("preset"):
                hw["preset"] = preset
            if preset != ddc.USER_PRESET:  # gains belong to the preset, don't override them
                for f in ("red", "green", "blue"):
                    hw.pop(f, None)
            entry = {"hw": hw}
            if d.soft_capable:
                entry["soft"] = self.current_soft(d)
            entries[d.key] = entry
        self.settings["profiles"][name.strip()] = {"displays": entries}
        save_settings(self.settings)
        self._fill_profiles()
        self.status("Профиль «%s» сохранён" % name)

    def delete_profile(self):
        name = self._selected_profile()
        if name and messagebox.askyesno(APP_NAME, "Удалить профиль «%s»?" % name):
            del self.settings["profiles"][name]
            save_settings(self.settings)
            self._fill_profiles()

    def profile_shortcut(self):
        name = self._selected_profile()
        if not name:
            return
        path = os.path.join(desktop_dir(), "%s — %s.lnk" % (APP_NAME, name))
        if make_shortcut(path, '--profile "%s"' % name, "PepeMonitor: профиль «%s»" % name):
            self.status("Ярлык «%s» создан на рабочем столе" % name)
        else:
            self.status("Не удалось создать ярлык", error=True)


def run_diag(args):
    import diag
    i = args.index("--diag")
    folder = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") \
        else os.path.join(desktop_dir(), "PepeMonitor-отчёты")
    base = diag.run(folder, VERSION, load_settings(), test_gamma="--no-gamma" not in args)
    print(base + ".txt")
    if EXE_PATH:  # windowed exe has no console to print to
        ctypes.windll.user32.MessageBoxW(None, "Отчёт сохранён:\n%s.txt" % base, APP_NAME, 0x40)
    return 0


def main():
    if "--diag" in sys.argv:
        sys.exit(run_diag(sys.argv[1:]))
    if "--profile" in sys.argv or "--restore" in sys.argv:
        sys.exit(run_cli(sys.argv[1:]))
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on 125%+ scaling
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
