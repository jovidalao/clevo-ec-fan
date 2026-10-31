import json
import os
import re
from pathlib import Path

from clevo_fan import DEFAULT_CURVE


class AppConfig:
    def __init__(self, path, data):
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path=None):
        config_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ClevoEcFanControl"
        path = Path(path) if path is not None else config_dir / "settings.json"
        data = {
            "curves": {"1": DEFAULT_CURVE, "2": DEFAULT_CURVE},
            "manual_duties": {"1": 50, "2": 50},
            "history_metric": "温度 °C",
            "history_range": "15 分钟",
            "mode": "automatic",
            "fan_modes": {"1": "automatic", "2": "automatic"},
            "shared_curve": DEFAULT_CURVE,
            "resume_mode": True,
            "link_curves": False,
            "link_manual": False,
            "close_to_tray": True,
            "window_geometry": "1100x860+60+40",
        }
        legacy_path = Path(__file__).with_name("gui_settings.json")
        try:
            if path.exists():
                stored = json.loads(path.read_text(encoding="utf-8"))
            elif legacy_path.exists():
                legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
                stored = {
                    "curves": {"1": legacy["curve"], "2": legacy["curve"]},
                    "resume_curve": legacy["resume_curve"],
                    "close_to_tray": True,
                }
            else:
                stored = {}
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            stored = {}

        if not isinstance(stored, dict):
            stored = {}

        def mapping(key):
            value = stored.get(key, {})
            return value if isinstance(value, dict) else {}

        mode = stored.get("mode")
        if mode not in ("automatic", "curve", "manual", "full"):
            mode = "curve" if stored.get("resume_curve", False) else "automatic"
        data["mode"] = mode
        for fan in ("1", "2"):
            try:
                data["curves"][fan] = cls.validate_curve(mapping("curves").get(fan, DEFAULT_CURVE))
            except (ValueError, TypeError, OverflowError):
                pass
            try:
                duty = int(mapping("manual_duties").get(fan, 50))
                if 0 <= duty <= 100:
                    data["manual_duties"][fan] = duty
            except (ValueError, TypeError, OverflowError):
                pass
            fan_mode = mapping("fan_modes").get(fan, mode)
            if fan_mode in ("automatic", "curve", "manual", "full"):
                data["fan_modes"][fan] = fan_mode
        try:
            data["shared_curve"] = cls.validate_curve(stored.get("shared_curve", data["curves"]["1"]))
        except (ValueError, TypeError, OverflowError):
            pass
        for key in ("resume_mode", "link_curves", "link_manual", "close_to_tray"):
            if isinstance(stored.get(key), bool):
                data[key] = stored[key]
        metric = stored.get("history_metric")
        if metric in ("温度 °C", "转速 %", "RPM"):
            data["history_metric"] = metric
        if stored.get("history_range") in ("3 分钟", "15 分钟", "1 小时", "6 小时", "24 小时"):
            data["history_range"] = stored["history_range"]
        geometry = stored.get("window_geometry")
        if isinstance(geometry, str):
            if re.fullmatch(r"\d{3,5}x\d{3,5}[+-]\d{1,6}[+-]\d{1,6}", geometry):
                data["window_geometry"] = geometry
        return cls(path, data)

    @staticmethod
    def validate_curve(curve):
        validated = tuple((int(temperature), int(duty)) for temperature, duty in curve)
        if not 2 <= len(validated) <= 8:
            raise ValueError("A curve must contain 2-8 points")
        if any(validated[index][0] >= validated[index + 1][0] for index in range(len(validated) - 1)):
            raise ValueError("Temperatures must be strictly increasing")
        if any(not 20 <= temperature <= 110 or not 20 <= duty <= 100 for temperature, duty in validated):
            raise ValueError("Invalid curve range")
        return validated

    def curves(self):
        if self.data["link_curves"]:
            return {fan: tuple(self.data["shared_curve"]) for fan in (1, 2)}
        return {int(fan): tuple(curve) for fan, curve in self.data["curves"].items()}

    def policy(self):
        curves = self.curves()
        return {fan: {"mode": self.data["fan_modes"][str(fan)],
                      "curve": curves[fan],
                      "duty": self.data["manual_duties"][str(fan)]} for fan in (1, 2)}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)
