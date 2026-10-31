"""Regression tests use a simulated controller; never load or write the EC driver."""
import json
import queue
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

from app_config import AppConfig
from clevo_fan import DEFAULT_CURVE, FanController, interpolate
from fan_policy import FanPolicy, MODE_NAMES
from clevo_fan_gui import FanControlApp, controller_worker
from telemetry_history import TelemetryHistory, HISTORY_RANGES


class FakeController:
    fan_count = 2

    def __init__(self):
        self.temperatures = {1: 60, 2: 40}
        self.calls = []
        self.fail_read = False

    def set_auto(self, fan):
        self.calls.append(("auto", fan))

    def set_duty(self, fan, duty):
        self.calls.append(("duty", fan, duty))

    def read_temperature_and_duty(self, fan):
        if self.fail_read:
            raise RuntimeError("sensor failed")
        return self.temperatures[fan], 50

    def status(self, **kwargs):
        return {"fans": [{"fan": fan, "temperature_c": temp, "duty_percent": 50, "rpm": 2000}
                         for fan, temp in self.temperatures.items()]}


def settings(first="curve", second="automatic"):
    return {fan: {"mode": mode, "curve": DEFAULT_CURVE, "duty": 35}
            for fan, mode in ((1, first), (2, second))}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return AppConfig.load(self.path)

    def test_legacy_and_intermediate_modes_migrate(self):
        for data, expected in (({"resume_curve": True}, "curve"),
                               ({"resume_curve": False}, "automatic"),
                               ({"mode": "manual"}, "manual"), ({"mode": "full"}, "full")):
            with self.subTest(data=data):
                self.assertEqual(self.load(data).data["fan_modes"], {"1": expected, "2": expected})

    def test_all_modes_and_mixed_survive_reopen(self):
        for first in MODE_NAMES:
            for second in MODE_NAMES:
                config = self.load({"fan_modes": {"1": first, "2": second},
                                    "manual_duties": {"1": 31, "2": 72}, "link_manual": True})
                config.save()
                reopened = AppConfig.load(self.path)
                self.assertEqual(reopened.policy()[1]["mode"], first)
                self.assertEqual(reopened.policy()[2]["mode"], second)
                self.assertEqual(reopened.policy()[2]["duty"], 72)
                self.assertTrue(reopened.data["link_manual"])

    def test_corrupt_fields_are_isolated(self):
        config = self.load({"curves": {"1": None, "2": [[40, 40], [90, 100]]},
                            "manual_duties": {"1": "bad", "2": 70}, "mode": "full"})
        self.assertEqual(config.data["curves"]["1"], DEFAULT_CURVE)
        self.assertEqual(config.data["manual_duties"]["2"], 70)
        self.assertEqual(config.data["fan_modes"]["2"], "full")
        for value in (None, [], 1, {"curves": [], "manual_duties": None, "fan_modes": []}):
            self.load(value)

    def test_shared_curve_preserves_individual_curves(self):
        config = self.load({"curves": {"1": [[40, 25], [90, 100]], "2": [[40, 40], [90, 100]]},
                            "link_curves": True, "shared_curve": [[40, 60], [90, 100]]})
        self.assertEqual(config.curves()[1], config.curves()[2])
        config.data["link_curves"] = False
        self.assertNotEqual(config.curves()[1], config.curves()[2])

    def test_history_range_survives_reopen_and_invalid_value_is_defaulted(self):
        for name in HISTORY_RANGES:
            config = self.load({"history_range": name})
            config.save()
            self.assertEqual(AppConfig.load(self.path).data["history_range"], name)
        self.assertEqual(self.load({"history_range": "invalid"}).data["history_range"], "15 分钟")


class HistoryTests(unittest.TestCase):
    def test_keeps_more_than_three_minutes_and_prunes_by_time(self):
        history = TelemetryHistory()
        for timestamp in range(0, 1200, 2):
            history.append(timestamp, [{"fan": 1, "temperature_c": 50}])
        self.assertEqual(len(history.fans[1]), 600)
        history.append(90000, [{"fan": 2, "temperature_c": 40}])
        self.assertEqual(len(history.fans[1]), 0)

    def test_downsampling_preserves_hot_and_cold_peaks_in_time_order(self):
        history = TelemetryHistory()
        for timestamp, value in ((1, 40), (2, 99), (3, 20), (4, 42)):
            history.append(timestamp, [{"fan": 1, "temperature_c": value}])
        self.assertEqual(history.segments(1, "temperature_c", 10, 10, 1),
                         [[(1, 40), (2, 99), (3, 20), (4, 42)]])

    def test_missing_rpm_and_communication_gaps_are_not_connected(self):
        history = TelemetryHistory()
        for timestamp, rpm in ((0, 2000), (2, 2200), (4, None), (6, 2300), (8, 2400), (30, 2500)):
            history.append(timestamp, [{"fan": 1, "rpm": rpm}])
        self.assertEqual(history.segments(1, "rpm", 30, 40, 100),
                         [[(0, 2000), (2, 2200)], [(6, 2300), (8, 2400)], [(30, 2500)]])

    def test_dense_history_has_bounded_drawing_cost(self):
        history = TelemetryHistory()
        for index in range(2000):
            history.append(index / 100, [{"fan": 1, "temperature_c": index % 100}])
        segments = history.segments(1, "temperature_c", 20, 20, 20)
        self.assertLessEqual(sum(map(len, segments)), 80)
        self.assertEqual(max(value for segment in segments for _, value in segment), 99)


class MonitorTests(unittest.TestCase):
    def test_rpm_timeout_does_not_fail_temperature_and_cached_sample_is_reused(self):
        controller = FanController.__new__(FanController)
        controller.fan_count = 2
        controller.read_temperature_and_duty = Mock(side_effect=AssertionError("duplicate temperature read"))
        controller.ec = Mock()
        controller.ec.read_rpm.side_effect = [TimeoutError("RPM timeout"), 2400, 2100, 2500]
        readings = {1: (60, 40), 2: (45, 30)}
        status = controller.status(readings, tolerate_rpm_errors=True)
        self.assertIsNone(status["fans"][0]["rpm"])
        self.assertEqual(status["fans"][0]["temperature_c"], 60)
        self.assertTrue(status["warnings"])
        controller.status(readings, tolerate_rpm_errors=True, read_rpm=False)
        self.assertEqual(controller.ec.read_rpm.call_count, 2)
        recovered = controller.status(readings, tolerate_rpm_errors=True)
        self.assertFalse(recovered["warnings"])
        self.assertEqual(recovered["fans"][0]["rpm"], 2100)
        controller.read_temperature_and_duty.assert_not_called()

    def test_monitor_failure_does_not_reset_curve(self):
        controller = FakeController()
        commands, events = queue.Queue(), queue.Queue()
        commands.put(("configure", settings("curve", "manual")))

        def fail_monitor(**kwargs):
            commands.put(("shutdown", None))
            raise TimeoutError("monitor only")

        controller.status = fail_monitor
        with patch("clevo_fan_gui.FanController", return_value=controller):
            controller_worker(commands, events)
        event_names = [event for event, _ in list(events.queue)]
        self.assertIn("error", event_names)
        self.assertNotIn("fallback", event_names)
        self.assertIn(("duty", 1, interpolate(DEFAULT_CURVE, 60)), controller.calls)

    def test_full_speed_is_applied_even_when_temperature_read_fails(self):
        controller = FakeController()
        controller.fail_read = True
        commands, events = queue.Queue(), queue.Queue()
        commands.put(("configure", settings("full", "full")))
        commands.put(("shutdown", None))
        with patch("clevo_fan_gui.FanController", return_value=controller):
            controller_worker(commands, events)
        self.assertIn(("duty", 1, 100), controller.calls)
        self.assertIn(("duty", 2, 100), controller.calls)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.policy = FanPolicy(2)

    def test_manual_one_fan_leaves_other_automatic(self):
        self.policy.configure(settings("manual"))
        self.policy.update(self.controller)
        self.assertEqual(self.controller.calls, [("duty", 1, 35), ("auto", 2)])

    def test_shared_curve_uses_each_sensor(self):
        self.policy.configure(settings("curve", "curve"))
        self.policy.update(self.controller)
        self.assertEqual(self.policy.targets, {1: interpolate(DEFAULT_CURVE, 60), 2: 25})

    def test_emergency_hysteresis_survives_reconfiguration(self):
        self.controller.temperatures[1] = 95
        self.policy.configure(settings("curve", "manual"))
        self.policy.update(self.controller)
        self.assertEqual(self.policy.targets[1], 100)
        self.controller.temperatures[1] = 90
        self.policy.configure(settings("manual", "manual"))
        self.policy.update(self.controller)
        self.assertEqual(self.policy.targets[1], 100)
        self.assertEqual(self.policy.targets[2], 35)
        self.controller.temperatures[1] = 88
        self.policy.update(self.controller)
        self.assertEqual(self.policy.targets[1], 35)

    def test_no_manual_write_before_sensor_check(self):
        self.policy.configure(settings("manual", "manual"))
        self.controller.fail_read = True
        with self.assertRaises(RuntimeError):
            self.policy.update(self.controller)
        self.assertEqual(self.controller.calls, [])

    def test_worker_sensor_failure_falls_back_and_shutdown_restores(self):
        self.controller.fail_read = True
        commands, events = queue.Queue(), queue.Queue()
        commands.put(("configure", settings("manual", "manual")))
        commands.put(("shutdown", None))
        with patch("clevo_fan_gui.FanController", return_value=self.controller):
            controller_worker(commands, events)
        result = []
        while not events.empty():
            result.append(events.get())
        self.assertIn("fallback", [event for event, _ in result])
        self.assertFalse(any(call[0] == "duty" for call in self.controller.calls))
        self.assertEqual(self.controller.calls[-2:], [("auto", 1), ("auto", 2)])


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"
        self.path.write_text(json.dumps({"fan_modes": {"1": "curve", "2": "automatic"},
                                        "curves": {"1": [[40, 30], [90, 100]],
                                                   "2": [[40, 45], [90, 100]]}}), encoding="utf-8")
        self.config = AppConfig.load(self.path)
        self.root = tk.Tk()
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.controller = FakeController()
        for patcher in (patch("clevo_fan_gui.AppConfig.load", return_value=self.config),
                        patch("clevo_fan_gui.FanController", return_value=self.controller),
                        patch.object(FanControlApp, "setup_tray"),
                        patch("clevo_fan_gui.autostart.is_enabled", return_value=False),
                        patch("clevo_fan_gui.system_identity", return_value=("TEST", "SIMULATED"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = FanControlApp(self.root)
        self.addCleanup(self.cleanup_app)
        self.pump(lambda: self.app.connected)

    def pump(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(0.01)
        self.fail("GUI event deadline exceeded")

    def cleanup_app(self):
        if self.app.worker.is_alive():
            self.app.commands.put(("shutdown", None))
            self.app.worker.join(3)
        try:
            for timer in self.root.tk.call("after", "info"):
                self.root.after_cancel(timer)
            self.root.destroy()
        except tk.TclError:
            pass
        self.assertEqual(self.callback_errors, [])

    def test_startup_restores_and_manual_preserves_other_fan(self):
        self.assertEqual(self.app.active_modes, {1: "curve", 2: "automatic"})
        self.app.manual_vars[2].set(67)
        self.app.set_manual(2)
        self.assertEqual(self.app.active_modes, {1: "curve", 2: "manual"})
        self.assertEqual(json.loads(self.path.read_text())["fan_modes"], {"1": "curve", "2": "manual"})
        self.pump(lambda: ("duty", 2, 67) in self.controller.calls)

    def test_linked_manual_and_full_auto_modes(self):
        self.app.link_manual_var.set(True)
        self.app.toggle_manual_link()
        self.app.manual_vars[2].set(63)
        self.app.set_manual(2)
        self.assertEqual(self.app.manual_vars[1].get(), 63)
        self.assertEqual(self.app.active_modes, {1: "manual", 2: "manual"})
        self.app.full_speed()
        self.assertEqual(self.app.active_modes, {1: "full", 2: "full"})
        self.app.restore_auto()
        self.assertEqual(self.app.active_modes, {1: "automatic", 2: "automatic"})

    def test_linked_edit_and_unlink_restore_with_different_point_counts(self):
        original = self.config.data["curves"].copy()
        self.app.link_curves_var.set(True)
        self.app.toggle_curve_link()
        self.app.curve_vars[2][0][1].set(55)
        self.app.curve_changed(2)
        self.assertEqual(self.app.curve_values(1), self.app.curve_values(2))
        self.assertEqual(self.config.data["curves"], original)
        self.app.link_curves_var.set(False)
        self.app.toggle_curve_link()
        self.assertNotEqual(self.app.curve_values(1), self.app.curve_values(2))
        self.app.reset_curve(1)
        self.assertEqual(len(self.app.curve_vars[1]), len(DEFAULT_CURVE))

    def test_disabled_resume_does_not_reactivate_other_fan(self):
        self.config.data["resume_mode"] = False
        self.app.active_modes = {1: "automatic", 2: "automatic"}
        self.app.resume_saved_mode()
        self.app.manual_vars[2].set(45)
        self.app.set_manual(2)
        self.assertEqual(self.app.active_modes, {1: "automatic", 2: "manual"})

    def test_shutdown_completes_and_keeps_selected_modes(self):
        self.app.exit_app()
        self.app.worker.join(3)
        destroyed = []
        self.root.bind("<Destroy>", lambda event: destroyed.append(True) if event.widget == self.root else None)
        self.pump(lambda: bool(destroyed))
        self.assertEqual(json.loads(self.path.read_text())["fan_modes"]["1"], "curve")
        self.assertEqual(self.controller.calls[-2:], [("auto", 1), ("auto", 2)])

    def stop_worker_for_events(self):
        self.app.commands.put(("shutdown", None))
        self.app.worker.join(3)
        while not self.app.events.empty():
            self.app.events.get_nowait()

    def test_recovered_status_clears_old_error_and_handles_unknown_rpm(self):
        self.stop_worker_for_events()
        status = {**FanPolicy(2).snapshot(), **self.controller.status()}
        self.app.events.put(("error", "test timeout"))
        self.app.events.put(("status", status))
        self.app.process_events()
        self.assertIn("通信已恢复", self.app.status_label.cget("text"))
        self.assertFalse(self.app.communication_error)
        status["fans"][0]["rpm"] = None
        status["warnings"] = ["CPU RPM 暂不可用"]
        self.app.events.put(("status", status))
        self.app.process_events()
        self.assertIn("-- RPM", self.app.telemetry_labels[1].cget("text"))

    def test_fallback_preserves_saved_choice_and_range_changes_are_saved(self):
        self.stop_worker_for_events()
        self.app.events.put(("fallback", {**FanPolicy(2).snapshot(), "restored": True}))
        self.app.process_events()
        self.assertEqual(self.app.active_modes[1], "automatic")
        self.assertEqual(self.config.data["fan_modes"]["1"], "curve")
        self.app.range_var.set("24 小时")
        self.app.change_history_range()
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["history_range"], "24 小时")
        self.assertEqual(stored["fan_modes"]["1"], "curve")


if __name__ == "__main__":
    unittest.main()
