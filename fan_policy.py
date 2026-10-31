"""Fan policy state, independent of Tk and the EC driver."""
from app_config import AppConfig
from clevo_fan import interpolate

EMERGENCY_TEMPERATURE = 95
EMERGENCY_RELEASE_TEMPERATURE = 88
MODE_NAMES = {"automatic": "EC 自动", "curve": "温度曲线", "manual": "手动固定", "full": "全速"}


class FanPolicy:
    def __init__(self, fan_count):
        if fan_count not in (1, 2):
            raise ValueError("当前界面仅支持已验证的单/双风扇平台")
        self.fan_count = fan_count
        self.settings = {fan: {"mode": "automatic"} for fan in range(1, fan_count + 1)}
        self.emergency_fans = set()
        self.targets = {}
        self.applied_modes = {}

    def configure(self, settings):
        validated = {}
        for fan in self.settings:
            entry = dict(settings[fan])
            if entry["mode"] not in MODE_NAMES:
                raise ValueError("未知风扇模式")
            entry["curve"] = AppConfig.validate_curve(entry["curve"])
            if not isinstance(entry["duty"], int) or not 0 <= entry["duty"] <= 100:
                raise ValueError("手动转速必须在 0–100% 之间")
            validated[fan] = entry
        self.settings = validated

    def update(self, controller):
        # Read every controlled sensor before writing any custom duty.
        temperatures = {fan: controller.read_temperature_and_duty(fan)[0]
                        for fan, entry in self.settings.items() if entry["mode"] in ("curve", "manual")}
        for fan, temperature in temperatures.items():
            if not 10 <= temperature <= 110:
                raise ValueError(f"风扇 {fan} 温度读数无效")
        for fan, entry in self.settings.items():
            mode = entry["mode"]
            if mode == "automatic":
                if self.applied_modes.get(fan) != mode:
                    controller.set_auto(fan)
                self.emergency_fans.discard(fan)
                self.targets.pop(fan, None)
            else:
                if mode == "full":
                    target = 100
                else:
                    temperature = temperatures[fan]
                    if temperature >= EMERGENCY_TEMPERATURE:
                        self.emergency_fans.add(fan)
                    elif temperature <= EMERGENCY_RELEASE_TEMPERATURE:
                        self.emergency_fans.discard(fan)
                    target = (100 if fan in self.emergency_fans else
                              interpolate(entry["curve"], temperature) if mode == "curve" else entry["duty"])
                if self.targets.get(fan) != target or self.applied_modes.get(fan) != mode:
                    controller.set_duty(fan, target)
                self.targets[fan] = target
            self.applied_modes[fan] = mode

    def restore_auto(self, controller):
        failures = []
        for fan in self.settings:
            try:
                controller.set_auto(fan)
                self.settings[fan] = {"mode": "automatic"}
                self.applied_modes[fan] = "automatic"
                self.targets.pop(fan, None)
                self.emergency_fans.discard(fan)
            except Exception as error:
                failures.append(f"风扇 {fan}: {error}")
        return failures

    def snapshot(self):
        modes = {fan: entry["mode"] for fan, entry in self.settings.items()}
        return {"mode": next(iter(modes.values())) if len(set(modes.values())) == 1 else "mixed",
                "fan_modes": modes, "targets": dict(self.targets),
                "emergency_fans": sorted(self.emergency_fans)}
