import argparse
import ctypes
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import messagebox, ttk

import pystray
from PIL import Image, ImageDraw

import autostart
from app_config import AppConfig
from clevo_fan import DEFAULT_CURVE, FanController, acquire_mutex
from fan_policy import FanPolicy, MODE_NAMES, EMERGENCY_TEMPERATURE, EMERGENCY_RELEASE_TEMPERATURE
from platform_guard import compatibility_error, system_identity
from version import APP_NAME, APP_VERSION

FAN_NAMES = {1: "风扇 1 · CPU", 2: "风扇 2 · GPU"}
FAN_COLORS = {1: "#1971c2", 2: "#e8590c"}


def resource_path(relative):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative


def controller_worker(commands, events):
    controller = None
    policy = None
    try:
        controller = FanController()
        policy = FanPolicy(controller.fan_count)
        events.put(("ready", controller.fan_count))
        next_status = next_update = 0
        while True:
            try:
                command, payload = commands.get(timeout=0.1)
            except queue.Empty:
                command, payload = None, None
            if command == "shutdown":
                break
            now = time.monotonic()
            try:
                if command == "configure":
                    policy.configure(payload)
                    next_status = next_update = 0
                now = time.monotonic()
                if now >= next_update:
                    policy.update(controller)
                    next_update = now + 2
                if now >= next_status:
                    events.put(("status", {**policy.snapshot(), **controller.status()}))
                    next_status = now + 1.5
            except Exception as error:
                failures = policy.restore_auto(controller)
                # Discard queued custom settings after a hardware failure.
                shutdown_pending = False
                while True:
                    try:
                        queued, _ = commands.get_nowait()
                        shutdown_pending |= queued == "shutdown"
                    except queue.Empty:
                        break
                if shutdown_pending:
                    commands.put(("shutdown", None))
                events.put(("error", f"{error}；已尝试恢复 EC 自动" +
                            ("；恢复失败：" + "；".join(failures) if failures else "")))
                events.put(("fallback", policy.snapshot()))
                if failures:
                    break
                next_status = now + 1.5
    except Exception as error:
        events.put(("fatal", str(error)))
    finally:
        if controller is not None:
            fans = range(1, controller.fan_count + 1)
            for fan in fans:
                try:
                    controller.set_auto(fan)
                except Exception as error:
                    events.put(("error", f"恢复自动控制失败：{error}"))
        events.put(("stopped", None))


class FanControlApp:
    def __init__(self, root):
        self.root = root
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.config = AppConfig.load()
        self.active_modes = {1: "automatic", 2: "automatic"}
        self.manual_vars = {}
        self.fan_mode_vars = {}
        self.fan_cards = {}
        self.curve_editors = {}
        self.connected = False
        self.fan_count = 2
        self.edit_after = None
        self.telemetry_labels = {}
        self.target_labels = {}
        self.curve_vars = {1: [], 2: []}
        self.curve_canvases = {}
        self.drag_target = None
        self.history = {1: deque(maxlen=120), 2: deque(maxlen=120)}
        self.close_requested = False
        self.last_status = None
        self.tray_icon = None

        root.title(APP_NAME)
        root.geometry(self.config.data["window_geometry"])
        root.minsize(1040, 800)
        root.protocol("WM_DELETE_WINDOW", self.hide_window)
        icon = resource_path(Path("assets") / "icon.ico")
        if icon.exists():
            root.iconbitmap(str(icon))

        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Mode.TLabel", foreground="#087f5b")
        style.configure("Telemetry.TLabel", font=("Consolas", 12, "bold"))
        style.configure("Target.TLabel", foreground="#495057")
        style.configure("Warning.TLabel", foreground="#c92a2a")

        container = ttk.Frame(root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{APP_VERSION}", foreground="#6c757d").pack(side="left", padx=10, pady=(9, 0))
        self.mode_label = ttk.Label(header, text="正在连接 EC…", style="Mode.TLabel")
        self.mode_label.pack(side="right", pady=(8, 0))

        self.notebook = ttk.Notebook(container)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        control_tab = ttk.Frame(self.notebook, padding=10)
        monitor_tab = ttk.Frame(self.notebook, padding=14)
        settings_tab = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(control_tab, text="风扇控制")
        self.notebook.add(monitor_tab, text="实时监控")
        self.notebook.add(settings_tab, text="设置与关于")
        self.build_control_tab(control_tab)
        self.build_monitor_tab(monitor_tab)
        self.build_settings_tab(settings_tab)

        self.status_label = ttk.Label(container, text="直接访问 EC · 0x62 / 0x66", relief="sunken",
                                      padding=(8, 5), anchor="w")
        self.status_label.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        self.worker = threading.Thread(target=controller_worker, args=(self.commands, self.events),
                                       name="ClevoEcWorker", daemon=True)
        self.worker.start()
        self.setup_tray()
        root.after(100, self.process_events)

    def build_control_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(toolbar, text="应用双风扇曲线", command=self.start_curve).pack(side="left")
        ttk.Button(toolbar, text="两个风扇全速", command=self.full_speed).pack(side="left", padx=8)
        ttk.Button(toolbar, text="恢复 EC 自动", command=self.restore_auto).pack(side="left")
        ttk.Label(toolbar, text="曲线/手动模式均有 95°C 高温保护",
                  foreground="#6c757d").pack(side="right")

        fans = ttk.Frame(parent)
        fans.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        fans.columnconfigure(0, weight=1)
        fans.columnconfigure(1, weight=1)
        for fan in (1, 2):
            self.build_fan_card(fans, fan)

        links = ttk.Frame(parent)
        links.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.link_curves_var = tk.BooleanVar(value=self.config.data["link_curves"])
        self.link_manual_var = tk.BooleanVar(value=self.config.data["link_manual"])
        ttk.Checkbutton(links, text="CPU/GPU 共用同一条曲线", variable=self.link_curves_var,
                        command=self.toggle_curve_link).pack(side="left")
        ttk.Checkbutton(links, text="手动调速时同步两个风扇", variable=self.link_manual_var,
                        command=self.toggle_manual_link).pack(side="left", padx=18)
        ttk.Label(links, text="共用曲线按各自温度调速；编辑后自动保存生效", foreground="#6c757d").pack(side="left")

        self.curve_tabs = ttk.Notebook(parent)
        self.curve_tabs.grid(row=3, column=0, sticky="nsew")
        for fan in (1, 2):
            tab = ttk.Frame(self.curve_tabs, padding=10)
            self.curve_tabs.add(tab, text="CPU 曲线" if fan == 1 else "GPU 曲线")
            self.build_curve_editor(tab, fan)

    def build_fan_card(self, parent, fan):
        card = ttk.LabelFrame(parent, text=FAN_NAMES[fan], padding=12)
        card.grid(row=0, column=fan - 1, sticky="nsew", padx=(0, 6) if fan == 1 else (6, 0))
        self.fan_cards[fan] = card
        card.columnconfigure(0, weight=1)
        telemetry = ttk.Label(card, text="-- °C   -- RPM", style="Telemetry.TLabel")
        telemetry.grid(row=0, column=0, sticky="w")
        self.telemetry_labels[fan] = telemetry
        target = ttk.Label(card, text="EC 回报转速 --%", style="Target.TLabel")
        target.grid(row=1, column=0, sticky="w", pady=(3, 8))
        self.target_labels[fan] = target

        mode_var = tk.StringVar(value=MODE_NAMES["automatic"])
        self.fan_mode_vars[fan] = mode_var
        selector = ttk.Combobox(card, textvariable=mode_var, state="readonly",
                                values=tuple(MODE_NAMES.values()), width=12)
        selector.grid(row=4, column=0, sticky="w", pady=(8, 0))
        selector.bind("<<ComboboxSelected>>", lambda event: self.select_fan_mode(fan))
        ttk.Label(card, text="选择后生效").grid(row=4, column=1, pady=(8, 0))
        variable = tk.IntVar(value=self.config.data["manual_duties"][str(fan)])
        self.manual_vars[fan] = variable
        value_label = ttk.Label(card, text=f"{variable.get()}%", width=5, anchor="e")
        variable.trace_add("write", lambda *_: self.update_manual_label(variable, value_label))
        value_label.grid(row=2, column=1, sticky="e")
        slider = ttk.Scale(card, from_=0, to=100, variable=variable,
                           command=lambda value: value_label.configure(text=f"{int(float(value))}%"))
        slider.grid(row=2, column=0, sticky="ew")
        slider.bind("<ButtonRelease-1>", lambda event: self.set_manual(fan))
        precise = ttk.Spinbox(card, from_=0, to=100, width=6, textvariable=variable,
                              command=lambda: self.set_manual(fan))
        precise.grid(row=3, column=1, sticky="e", pady=(6, 0))
        precise.bind("<Return>", lambda event: self.set_manual(fan))
        ttk.Label(card, text="手动转速（松开生效）").grid(row=3, column=0, sticky="w", pady=(6, 0))

    def build_curve_editor(self, parent, fan):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        canvas = tk.Canvas(parent, background="white", highlightthickness=1,
                           highlightbackground="#dee2e6", width=650, height=330)
        canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        canvas.bind("<Configure>", lambda event, fan=fan: self.draw_curve(fan))
        canvas.bind("<Button-1>", lambda event, fan=fan: self.start_drag(fan, event))
        canvas.bind("<B1-Motion>", lambda event, fan=fan: self.drag_curve_point(fan, event))
        canvas.bind("<ButtonRelease-1>", lambda event: setattr(self, "drag_target", None))
        self.curve_canvases[fan] = canvas

        editor = ttk.Frame(parent)
        editor.grid(row=0, column=1, sticky="ns")
        self.curve_editors[fan] = editor
        self.populate_curve_editor(fan)

    def populate_curve_editor(self, fan):
        editor = self.curve_editors[fan]
        for child in editor.winfo_children():
            child.destroy()
        self.curve_vars[fan] = []
        ttk.Label(editor, text="节点").grid(row=0, column=0, padx=5, pady=4)
        ttk.Label(editor, text="温度 °C").grid(row=0, column=1, padx=5, pady=4)
        ttk.Label(editor, text="转速 %").grid(row=0, column=2, padx=5, pady=4)
        for index, (temperature, duty) in enumerate(self.config.curves()[fan]):
            temperature_var = tk.IntVar(value=temperature)
            duty_var = tk.IntVar(value=duty)
            self.curve_vars[fan].append((temperature_var, duty_var))
            ttk.Label(editor, text=str(index + 1)).grid(row=index + 1, column=0, padx=5, pady=4)
            for column, variable, upper in ((1, temperature_var, 110), (2, duty_var, 100)):
                spinbox = ttk.Spinbox(editor, from_=20, to=upper, width=7, textvariable=variable,
                                      command=lambda fan=fan: self.curve_changed(fan))
                spinbox.grid(row=index + 1, column=column, padx=5, pady=4)
                spinbox.bind("<FocusOut>", lambda event, fan=fan: self.curve_changed(fan))
                spinbox.bind("<Return>", lambda event, fan=fan: self.curve_changed(fan))
        ttk.Label(editor, text="可拖动左侧节点", foreground="#6c757d").grid(
            row=len(self.curve_vars[fan]) + 1, column=0, columnspan=3, pady=6)
        ttk.Button(editor, text="恢复此风扇默认曲线",
                   command=lambda: self.reset_curve(fan)).grid(
            row=len(self.curve_vars[fan]) + 2, column=0, columnspan=3, sticky="ew", pady=4)

    def build_monitor_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(toolbar, text="历史指标").pack(side="left")
        self.metric_var = tk.StringVar(value=self.config.data["history_metric"])
        metric = ttk.Combobox(toolbar, textvariable=self.metric_var, state="readonly", width=12,
                              values=("温度 °C", "转速 %", "RPM"))
        metric.pack(side="left", padx=8)
        metric.bind("<<ComboboxSelected>>", self.change_metric)
        ttk.Label(toolbar, text="蓝色：CPU　橙色：GPU　最近约 3 分钟",
                  foreground="#6c757d").pack(side="right")
        self.monitor_summary = ttk.Label(parent, text="等待传感器数据…", style="Telemetry.TLabel")
        self.monitor_summary.grid(row=1, column=0, sticky="w", pady=14)
        self.history_canvas = tk.Canvas(parent, background="white", highlightthickness=1,
                                        highlightbackground="#dee2e6")
        self.history_canvas.grid(row=2, column=0, sticky="nsew")
        self.history_canvas.bind("<Configure>", lambda event: self.draw_history())

    def build_settings_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        startup = ttk.LabelFrame(parent, text="启动与后台", padding=14)
        startup.grid(row=0, column=0, sticky="ew")
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        self.close_to_tray_var = tk.BooleanVar(value=self.config.data["close_to_tray"])
        self.resume_mode_var = tk.BooleanVar(value=self.config.data["resume_mode"])
        ttk.Checkbutton(startup, text="登录 Windows 时以后台方式启动（管理员任务）",
                        variable=self.autostart_var, command=self.toggle_autostart).grid(
            row=0, column=0, sticky="w", pady=5)
        ttk.Checkbutton(startup, text="关闭窗口时最小化到系统托盘",
                        variable=self.close_to_tray_var, command=self.save_preferences).grid(
            row=1, column=0, sticky="w", pady=5)
        ttk.Checkbutton(startup, text="每次打开时恢复上次 CPU/GPU 模式（含手动和全速）",
                        variable=self.resume_mode_var, command=self.save_preferences).grid(
            row=2, column=0, sticky="w", pady=5)
        ttk.Label(startup, text="取消恢复时以 EC 自动启动。托盘“退出”恢复 EC 自动，下次仍记住所选模式。",
                  foreground="#6c757d").grid(row=3, column=0, sticky="w", pady=(8, 0))

        safety = ttk.LabelFrame(parent, text="硬件与安全", padding=14)
        safety.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        try:
            identity = " ".join(system_identity())
        except OSError:
            identity = "未知机型"
        ttk.Label(safety, text=f"当前平台：{identity}").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Label(safety, text="协议：ACPI EC 0x62/0x66 · 双风扇 · 独立温度曲线").grid(
            row=1, column=0, sticky="w", pady=3)
        ttk.Label(safety, text=f"高温保护：{EMERGENCY_TEMPERATURE}°C 强制全速，"
                               f"{EMERGENCY_RELEASE_TEMPERATURE}°C 解除").grid(
            row=2, column=0, sticky="w", pady=3)
        ttk.Label(safety, text="仅默认启用已实机验证的 HASEE CNP6S；其他型号请勿直接写入 EC。",
                  style="Warning.TLabel").grid(row=3, column=0, sticky="w", pady=3)

        about = ttk.LabelFrame(parent, text="关于", padding=14)
        about.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        ttk.Label(about, text=f"{APP_NAME} v{APP_VERSION}").grid(row=0, column=0, sticky="w")
        ttk.Label(about, text="开源、无遥测、离线运行。设置仅保存在本机。",
                  foreground="#6c757d").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Button(about, text="打开配置目录", command=self.open_config_directory).grid(
            row=2, column=0, sticky="w", pady=5)

    def curve_values(self, fan):
        try:
            curve = tuple((temperature.get(), duty.get()) for temperature, duty in self.curve_vars[fan])
            return AppConfig.validate_curve(curve)
        except (tk.TclError, ValueError, TypeError) as error:
            raise ValueError("曲线需包含 2–8 个节点，温度严格递增；温度 20–110°C，转速 20–100%") from error

    @staticmethod
    def update_manual_label(variable, label):
        try:
            label.configure(text=f"{int(variable.get())}%")
        except (tk.TclError, ValueError):
            label.configure(text="--%")

    def reset_curve(self, fan):
        if self.link_curves_var.get():
            self.config.data["shared_curve"] = DEFAULT_CURVE
            fans = (1, 2)
        else:
            self.config.data["curves"][str(fan)] = DEFAULT_CURVE
            fans = (fan,)
        for selected in fans:
            self.populate_curve_editor(selected)
            self.draw_curve(selected)
        self.curve_changed(fan)

    def curve_changed(self, fan):
        try:
            curve = self.curve_values(fan)
        except ValueError as error:
            self.status_label.configure(text=str(error))
            return
        if self.link_curves_var.get():
            self.config.data["shared_curve"] = curve
            other = 3 - fan
            self.populate_curve_editor(other)
            self.draw_curve(other)
        else:
            self.config.data["curves"][str(fan)] = curve
        self.draw_curve(fan)
        self.save_preferences()
        # Coalesce dragging into one policy update when the pointer pauses.
        if self.edit_after is not None:
            self.root.after_cancel(self.edit_after)
        self.edit_after = self.root.after(200, self.apply_curve_edits)

    def apply_curve_edits(self):
        if self.edit_after is not None:
            self.root.after_cancel(self.edit_after)
        self.edit_after = None
        if any(mode == "curve" for mode in self.active_modes.values()):
            self.send_policy()

    def toggle_curve_link(self):
        linked = self.link_curves_var.get()
        self.config.data["link_curves"] = linked
        for fan in (1, 2):
            self.populate_curve_editor(fan)
            self.draw_curve(fan)
        self.curve_tabs.tab(0, text="共用曲线 · CPU" if linked else "CPU 曲线")
        self.curve_tabs.tab(1, text="共用曲线 · GPU" if linked else "GPU 曲线")
        self.save_preferences()
        self.apply_curve_edits()

    def toggle_manual_link(self):
        self.config.data["link_manual"] = self.link_manual_var.get()
        self.save_preferences()
        self.status_label.configure(text="下次调整手动转速时同步两个风扇" if self.link_manual_var.get()
                                    else "手动转速可分别调整")

    def select_fan_mode(self, fan):
        mode = next(key for key, name in MODE_NAMES.items() if name == self.fan_mode_vars[fan].get())
        if mode == "manual":
            self.set_manual(fan)
        else:
            self.set_modes({fan: mode})

    def set_manual(self, fan):
        try:
            percent = self.manual_vars[fan].get()
            if not 0 <= percent <= 100:
                raise ValueError()
        except (tk.TclError, ValueError):
            self.status_label.configure(text="请输入 0–100 的整数转速")
            return
        fans = range(1, self.fan_count + 1) if self.link_manual_var.get() else (fan,)
        for selected in fans:
            self.manual_vars[selected].set(percent)
            self.config.data["manual_duties"][str(selected)] = percent
        self.set_modes({selected: "manual" for selected in fans})

    def full_speed(self):
        self.set_modes({fan: "full" for fan in range(1, self.fan_count + 1)})

    def restore_auto(self):
        self.set_modes({fan: "automatic" for fan in range(1, self.fan_count + 1)})

    def start_curve(self):
        self.set_modes({fan: "curve" for fan in range(1, self.fan_count + 1)})

    def set_modes(self, modes):
        if not self.connected or self.close_requested:
            self.status_label.configure(text="EC 尚未就绪，暂不能应用控制")
            return
        # Validate all visible curve inputs before saving or changing modes.
        try:
            curves = {fan: self.curve_values(fan) for fan in (1, 2)}
        except ValueError as error:
            if "curve" in modes.values():
                messagebox.showerror("曲线无效", str(error), parent=self.root)
                return
            curves = {}
        if self.link_curves_var.get():
            if curves:
                self.config.data["shared_curve"] = curves[1]
        else:
            for fan, curve in curves.items():
                self.config.data["curves"][str(fan)] = curve
        for fan, mode in modes.items():
            self.active_modes[fan] = mode
            self.fan_mode_vars[fan].set(MODE_NAMES[mode])
        for fan, mode in self.active_modes.items():
            self.config.data["fan_modes"][str(fan)] = mode
        values = set(self.config.data["fan_modes"].values())
        self.config.data["mode"] = next(iter(values)) if len(values) == 1 else "mixed"
        self.save_preferences()
        self.send_policy()

    def send_policy(self):
        if self.connected and not self.close_requested:
            settings = self.config.policy()
            for fan, mode in self.active_modes.items():
                settings[fan]["mode"] = mode
            self.commands.put(("configure", settings))
            self.status_label.configure(text="正在应用设置 · 曲线/手动模式有高温保护")

    def resume_saved_mode(self):
        if self.config.data["resume_mode"]:
            self.active_modes = {int(fan): mode for fan, mode in self.config.data["fan_modes"].items()}
            for fan, mode in self.active_modes.items():
                self.fan_mode_vars[fan].set(MODE_NAMES[mode])
            self.send_policy()
        else:
            # Keep saved preferences until the user explicitly chooses another mode.
            for variable in self.fan_mode_vars.values():
                variable.set(MODE_NAMES["automatic"])

    def change_metric(self, event=None):
        self.config.data["history_metric"] = self.metric_var.get()
        self.save_preferences()
        self.draw_history()

    def save_preferences(self):
        self.config.data["close_to_tray"] = self.close_to_tray_var.get()
        self.config.data["resume_mode"] = self.resume_mode_var.get()
        self.config.data["link_curves"] = self.link_curves_var.get()
        self.config.data["link_manual"] = self.link_manual_var.get()
        self.config.data["history_metric"] = self.metric_var.get()
        self.config.data["window_geometry"] = self.root.geometry()
        try:
            self.config.save()
        except OSError as error:
            self.status_label.configure(text=f"保存设置失败：{error}")

    def toggle_autostart(self):
        desired = self.autostart_var.get()
        self.status_label.configure(text="正在更新开机启动任务…")

        def update():
            try:
                autostart.set_enabled(desired)
                self.events.put(("autostart", desired))
            except Exception as error:
                self.events.put(("autostart_error", (not desired, str(error))))

        threading.Thread(target=update, daemon=True).start()

    def open_config_directory(self):
        self.config.path.parent.mkdir(parents=True, exist_ok=True)
        os.startfile(self.config.path.parent)

    def curve_points(self, fan):
        canvas = self.curve_canvases[fan]
        width, height = max(canvas.winfo_width(), 400), max(canvas.winfo_height(), 250)
        left, right, top, bottom = 48, 22, 22, 42
        return [
            (left + (temperature - 20) / 90 * (width - left - right),
             top + (100 - duty) / 100 * (height - top - bottom))
            for temperature, duty in self.curve_values(fan)
        ]

    def draw_curve(self, fan):
        canvas = self.curve_canvases.get(fan)
        if canvas is None:
            return
        try:
            curve = self.curve_values(fan)
        except ValueError:
            return
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 400), max(canvas.winfo_height(), 250)
        left, right, top, bottom = 48, 22, 22, 42
        for duty in (0, 25, 50, 75, 100):
            y = top + (100 - duty) / 100 * (height - top - bottom)
            canvas.create_line(left, y, width - right, y, fill="#e9ecef")
            canvas.create_text(left - 9, y, text=str(duty), anchor="e", fill="#6c757d", font=("Consolas", 9))
        for temperature in (20, 40, 60, 80, 100):
            x = left + (temperature - 20) / 90 * (width - left - right)
            canvas.create_line(x, top, x, height - bottom, fill="#f1f3f5")
            canvas.create_text(x, height - bottom + 17, text=f"{temperature}°C", fill="#6c757d", font=("Consolas", 9))
        points = self.curve_points(fan)
        if len(points) > 1:
            canvas.create_line(*points, fill=FAN_COLORS[fan], width=3)
        for index, (x, y) in enumerate(points):
            canvas.create_oval(x - 7, y - 7, x + 7, y + 7, fill="white", outline=FAN_COLORS[fan], width=3)
            canvas.create_text(x, y - 15, text=f"{curve[index][1]}%", fill=FAN_COLORS[fan],
                               font=("Microsoft YaHei UI", 9, "bold"))
        canvas.create_text(left, 8, text="转速 %", anchor="w", fill="#495057")

    def start_drag(self, fan, event):
        points = self.curve_points(fan)
        distances = [math.hypot(event.x - x, event.y - y) for x, y in points]
        index = min(range(len(distances)), key=distances.__getitem__)
        if distances[index] <= 16:
            self.drag_target = (fan, index)

    def drag_curve_point(self, fan, event):
        if self.drag_target is None or self.drag_target[0] != fan:
            return
        canvas = self.curve_canvases[fan]
        width, height = max(canvas.winfo_width(), 400), max(canvas.winfo_height(), 250)
        left, right, top, bottom = 48, 22, 22, 42
        temperature = round(20 + (event.x - left) / (width - left - right) * 90)
        duty = round(100 - (event.y - top) / (height - top - bottom) * 100)
        index = self.drag_target[1]
        minimum = self.curve_vars[fan][index - 1][0].get() + 1 if index > 0 else 20
        maximum = self.curve_vars[fan][index + 1][0].get() - 1 if index < len(self.curve_vars[fan]) - 1 else 110
        self.curve_vars[fan][index][0].set(max(minimum, min(maximum, temperature)))
        self.curve_vars[fan][index][1].set(max(20, min(100, duty)))
        self.curve_changed(fan)

    def draw_history(self):
        if not hasattr(self, "history_canvas"):
            return
        canvas = self.history_canvas
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 500), max(canvas.winfo_height(), 300)
        left, right, top, bottom = 58, 24, 28, 42
        metric = self.metric_var.get()
        key = {"温度 °C": "temperature_c", "转速 %": "duty_percent", "RPM": "rpm"}[metric]
        maximum = 110 if key == "temperature_c" else 100 if key == "duty_percent" else 8000
        for step in range(6):
            value = round(maximum * step / 5)
            y = top + (maximum - value) / maximum * (height - top - bottom)
            canvas.create_line(left, y, width - right, y, fill="#e9ecef")
            canvas.create_text(left - 10, y, text=str(value), anchor="e", fill="#6c757d", font=("Consolas", 9))
        for fan in (1, 2):
            entries = list(self.history[fan])
            if len(entries) < 2:
                continue
            points = []
            for index, entry in enumerate(entries):
                x = left + index / 119 * (width - left - right)
                value = min(maximum, entry[key])
                y = top + (maximum - value) / maximum * (height - top - bottom)
                points.append((x, y))
            canvas.create_line(*points, fill=FAN_COLORS[fan], width=2)
        canvas.create_text(left, 10, text=metric, anchor="w", fill="#495057")

    def setup_tray(self):
        image = Image.new("RGBA", (64, 64), (25, 113, 194, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((10, 10, 54, 54), outline="white", width=4)
        for endpoint in ((32, 14), (50, 32), (32, 50), (14, 32)):
            draw.line((32, 32, *endpoint), fill="white", width=5)
        draw.ellipse((26, 26, 38, 38), fill="white")
        menu = pystray.Menu(
            pystray.MenuItem("打开控制面板", lambda icon, item: self.root.after(0, self.show_window), default=True),
            pystray.MenuItem("应用双风扇曲线", lambda icon, item: self.root.after(0, self.start_curve)),
            pystray.MenuItem("恢复 EC 自动", lambda icon, item: self.root.after(0, self.restore_auto)),
            pystray.MenuItem("双风扇全速", lambda icon, item: self.root.after(0, self.full_speed)),
            pystray.MenuItem("退出", lambda icon, item: self.root.after(0, self.exit_app)),
        )
        self.tray_icon = pystray.Icon("ClevoEcFanControl", image, APP_NAME, menu)
        threading.Thread(target=self.tray_icon.run, name="ClevoEcTray", daemon=True).start()

    def hide_window(self):
        self.save_preferences()
        if self.close_to_tray_var.get():
            self.root.withdraw()
        else:
            self.exit_app()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def exit_app(self):
        if self.close_requested:
            return
        self.save_preferences()
        self.close_requested = True
        if self.edit_after is not None:
            self.root.after_cancel(self.edit_after)
            self.edit_after = None
        self.mode_label.configure(text="正在恢复自动…")
        self.status_label.configure(text="正在恢复 EC 自动控制并退出…")
        if not self.worker.is_alive():
            if self.tray_icon is not None:
                self.tray_icon.stop()
            self.root.destroy()
            return
        self.commands.put(("shutdown", None))

    def process_events(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "ready":
                    self.connected = True
                    self.fan_count = payload
                    self.status_label.configure(text=f"EC 已连接 · 检测到 {payload} 个风扇")
                    if payload == 1:
                        for child in self.fan_cards[2].winfo_children():
                            try:
                                child.configure(state="disabled")
                            except tk.TclError:
                                pass
                    self.resume_saved_mode()
                elif event == "status":
                    self.last_status = payload
                    mode = " / ".join(f"{'CPU' if fan == 1 else 'GPU'} {MODE_NAMES[value]}"
                                      for fan, value in payload["fan_modes"].items())
                    if payload["emergency_fans"]:
                        mode += " · 高温保护"
                    self.mode_label.configure(text=mode)
                    if self.tray_icon is not None:
                        self.tray_icon.title = f"{APP_NAME} · {mode}"
                    summaries = []
                    for fan_data in payload["fans"]:
                        fan = fan_data["fan"]
                        self.history[fan].append(fan_data)
                        self.telemetry_labels[fan].configure(
                            text=f"{fan_data['temperature_c']:2d} °C   {fan_data['rpm']:4d} RPM")
                        target = payload["targets"].get(fan)
                        suffix = f" · 目标 {target}%" if target is not None else ""
                        self.target_labels[fan].configure(text=f"EC 回报转速 {fan_data['duty_percent']}%{suffix}")
                        summaries.append(f"{'CPU' if fan == 1 else 'GPU'} {fan_data['temperature_c']}°C / {fan_data['rpm']} RPM")
                    self.monitor_summary.configure(text="　　".join(summaries))
                    self.draw_history()
                elif event == "error":
                    self.status_label.configure(text=f"错误：{payload}")
                elif event == "fallback":
                    for fan, mode in payload["fan_modes"].items():
                        self.active_modes[fan] = mode
                        self.config.data["fan_modes"][str(fan)] = mode
                        self.fan_mode_vars[fan].set(MODE_NAMES[mode])
                    self.config.data["mode"] = payload["mode"]
                    self.mode_label.configure(text="控制出错 · 已回退 EC 自动")
                    self.save_preferences()
                elif event == "fatal":
                    self.status_label.configure(text=f"EC 连接失败：{payload}")
                    messagebox.showerror("EC 连接失败", payload, parent=self.root)
                elif event == "autostart":
                    self.autostart_var.set(payload)
                    self.status_label.configure(text="开机启动已启用" if payload else "开机启动已关闭")
                elif event == "autostart_error":
                    actual, error = payload
                    self.autostart_var.set(actual)
                    self.status_label.configure(text=f"开机启动设置失败：{error}")
                elif event == "stopped":
                    self.connected = False
                    if not self.close_requested:
                        self.mode_label.configure(text="EC 控制已停止")
                        continue
                    if self.tray_icon is not None:
                        self.tray_icon.stop()
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--allow-unsupported", action="store_true")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    args = parser.parse_args()
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass

    error = compatibility_error(args.allow_unsupported)
    if error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("机型兼容性保护", error)
        root.destroy()
        return
    try:
        kernel32, mutex = acquire_mutex()
    except Exception:
        if not args.background:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("已在后台运行", "请从系统托盘的蓝色风扇图标打开控制面板。")
            root.destroy()
        return

    try:
        root = tk.Tk()
        if args.background:
            root.withdraw()
        app = FanControlApp(root)
        root.mainloop()
    finally:
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
