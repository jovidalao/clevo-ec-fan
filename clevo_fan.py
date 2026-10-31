import argparse
import ctypes
import json
import time

from ec_probe import EmbeddedController

DEFAULT_CURVE = ((40, 25), (55, 35), (65, 50), (75, 70), (85, 90), (90, 100))


class FanController:
    def __init__(self):
        self.ec = EmbeddedController()
        if not self.ec.driver_open:
            raise RuntimeError("InpOutx64 driver could not be opened")
        previous_count = None
        for _ in range(8):
            fan_count = self.ec.read_register(0xC8)
            if 1 <= fan_count <= 4 and fan_count == previous_count:
                self.fan_count = fan_count
                break
            previous_count = fan_count if 1 <= fan_count <= 4 else None
            time.sleep(0.02)
        else:
            raise RuntimeError("Could not read a consistent, valid fan count")

    def selected_fans(self, fan):
        if fan == 0:
            return range(1, self.fan_count + 1)
        if not 1 <= fan <= self.fan_count:
            raise ValueError(f"Fan must be between 1 and {self.fan_count}")
        return (fan,)

    def set_duty(self, fan, percent):
        if not 0 <= percent <= 100:
            raise ValueError("Duty must be between 0 and 100")
        duty = round(percent * 255 / 100)
        for attempt in range(3):
            try:
                self.ec.send_command(0x99)
                self.ec.send_data(fan)
                self.ec.send_data(duty)
                return
            except TimeoutError:
                if attempt == 2:
                    raise
                time.sleep(0.02)

    def set_auto(self, fan):
        for attempt in range(3):
            try:
                self.ec.send_command(0x99)
                self.ec.send_data(0xFF)
                self.ec.send_data(fan)
                return
            except TimeoutError:
                if attempt == 2:
                    raise
                time.sleep(0.02)

    def read_temperature_and_duty(self, fan):
        previous = None
        for _ in range(8):
            reading = self.ec.read_temperature_and_duty(fan)
            temperature = reading[0]
            if 10 <= temperature <= 110 and previous is not None and abs(temperature - previous[0]) <= 5:
                return reading
            previous = reading if 10 <= temperature <= 110 else None
            time.sleep(0.02)
        raise RuntimeError(f"Could not read a consistent, valid temperature for fan {fan}")

    def status(self, readings=None, *, tolerate_rpm_errors=False, read_rpm=True):
        fans = []
        warnings = []
        if not hasattr(self, "_rpm_cache"):
            self._rpm_cache = {}
        for fan in range(1, self.fan_count + 1):
            temperature, duty = (readings[fan] if readings is not None else
                                 self.read_temperature_and_duty(fan))
            if read_rpm:
                try:
                    self._rpm_cache[fan] = (self.ec.read_rpm(fan), None)
                except (TimeoutError, OSError) as error:
                    if not tolerate_rpm_errors:
                        raise
                    self._rpm_cache[fan] = (None, str(error))
            rpm, error = self._rpm_cache.get(fan, (None, None))
            if error:
                warnings.append(f"风扇 {fan} RPM 暂不可用：{error}")
            fans.append({
                "fan": fan,
                "temperature_c": temperature,
                "duty_percent": duty,
                "rpm": rpm,
            })
        return {"fans": fans, "warnings": warnings}

    def test(self):
        try:
            for fan in range(1, self.fan_count + 1):
                self.set_duty(fan, 100)
            time.sleep(3)
            print(json.dumps({"state": "full_speed", **self.status()}, indent=2))
        finally:
            for fan in range(1, self.fan_count + 1):
                self.set_auto(fan)
            time.sleep(2)
            print(json.dumps({"state": "automatic", **self.status()}, indent=2))


def interpolate(curve, temperature):
    if temperature <= curve[0][0]:
        return curve[0][1]
    for (low_temp, low_duty), (high_temp, high_duty) in zip(curve, curve[1:]):
        if temperature <= high_temp:
            ratio = (temperature - low_temp) / (high_temp - low_temp)
            return round(low_duty + ratio * (high_duty - low_duty))
    return curve[-1][1]


def parse_curve(value):
    try:
        curve = tuple(tuple(map(int, point.split(":"))) for point in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("Curve format: 40:25,55:35,65:50,75:70,85:90,90:100") from error
    if any(len(point) != 2 for point in curve):
        raise argparse.ArgumentTypeError("Each curve point must be temperature:duty")
    if any(not 0 <= point[0] <= 120 or not 20 <= point[1] <= 100 for point in curve):
        raise argparse.ArgumentTypeError("Temperature must be 0-120 and curve duty must be 20-100")
    if any(curve[index][0] >= curve[index + 1][0] for index in range(len(curve) - 1)):
        raise argparse.ArgumentTypeError("Curve temperatures must be strictly increasing")
    return curve


def run_curve(controller, fans, curve, interval, duration):
    selected = tuple(controller.selected_fans(fans))
    last_duties = {}
    started = time.monotonic()
    try:
        while duration == 0 or time.monotonic() - started < duration:
            states = []
            for fan in selected:
                temperature, reported_duty = controller.read_temperature_and_duty(fan)
                target = interpolate(curve, temperature)
                if last_duties.get(fan) != target:
                    controller.set_duty(fan, target)
                    last_duties[fan] = target
                states.append({
                    "fan": fan,
                    "temperature_c": temperature,
                    "reported_duty_percent": reported_duty,
                    "target_duty_percent": target,
                })
            print(json.dumps({"state": "curve", "fans": states}), flush=True)
            time.sleep(interval)
    finally:
        for fan in selected:
            controller.set_auto(fan)
        print(json.dumps({"state": "automatic", "fans": list(selected)}), flush=True)


def acquire_mutex():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\ClevoEcFanController")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        raise RuntimeError("Another clevo_fan.py instance is already running")
    return kernel32, handle


def main():
    parser = argparse.ArgumentParser(description="Direct Clevo EC fan control")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Read temperatures, duty and RPM")
    subparsers.add_parser("test", help="Run both fans at full speed briefly, then restore automatic control")

    for command in ("set", "auto", "curve"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--fan", type=int, default=0, help="Fan number; 0 controls all fans")
        if command == "set":
            subparser.add_argument("percent", type=int)
        if command == "curve":
            subparser.add_argument("--curve", type=parse_curve, default=DEFAULT_CURVE)
            subparser.add_argument("--interval", type=float, default=2.0)
            subparser.add_argument("--duration", type=float, default=0.0, help="Seconds; 0 runs until Ctrl+C")

    args = parser.parse_args()
    kernel32, mutex = acquire_mutex()
    try:
        controller = FanController()
        if args.command == "status":
            print(json.dumps(controller.status(), indent=2))
        elif args.command == "test":
            controller.test()
        elif args.command == "set":
            for fan in controller.selected_fans(args.fan):
                controller.set_duty(fan, args.percent)
            print(json.dumps({"state": "manual", "duty_percent": args.percent, **controller.status()}, indent=2))
        elif args.command == "auto":
            for fan in controller.selected_fans(args.fan):
                controller.set_auto(fan)
            print(json.dumps({"state": "automatic", **controller.status()}, indent=2))
        elif args.command == "curve":
            run_curve(controller, args.fan, args.curve, args.interval, args.duration)
    finally:
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
