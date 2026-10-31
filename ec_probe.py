import ctypes
import json
import time
from pathlib import Path

EC_DATA = 0x62
EC_COMMAND = 0x66
EC_IBF = 0x02
EC_OBF = 0x01
TIMEOUT = 0.1


class EmbeddedController:
    def __init__(self):
        dll_path = Path(__file__).with_name("inpoutx64.dll")
        self.lib = ctypes.WinDLL(str(dll_path))
        self.lib.DlPortReadPortUchar.argtypes = [ctypes.c_uint16]
        self.lib.DlPortReadPortUchar.restype = ctypes.c_uint8
        self.lib.DlPortWritePortUchar.argtypes = [ctypes.c_uint16, ctypes.c_uint8]
        self.lib.DlPortWritePortUchar.restype = None
        self.lib.IsInpOutDriverOpen.restype = ctypes.c_int

    @property
    def driver_open(self):
        return bool(self.lib.IsInpOutDriverOpen())

    def read_port(self, port):
        return self.lib.DlPortReadPortUchar(port)

    def write_port(self, port, value):
        self.lib.DlPortWritePortUchar(port, value)

    def wait_for(self, mask, expected):
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if (self.read_port(EC_COMMAND) & mask) == expected:
                return
            time.sleep(0.0001)
        raise TimeoutError(f"EC status timeout: mask=0x{mask:02X}, expected=0x{expected:02X}")

    def send_command(self, command):
        time.sleep(0.005)
        self.wait_for(EC_IBF, 0)
        self.write_port(EC_COMMAND, command)

    def send_data(self, value):
        self.wait_for(EC_IBF, 0)
        self.write_port(EC_DATA, value)

    def read_data(self):
        self.wait_for(EC_OBF, EC_OBF)
        return self.read_port(EC_DATA)

    def read_register(self, address):
        for attempt in range(3):
            try:
                self.send_command(0x80)
                self.send_data(address)
                return self.read_data()
            except TimeoutError:
                if attempt == 2:
                    raise
                time.sleep(0.02)

    def read_temperature_and_duty(self, fan):
        for attempt in range(3):
            try:
                self.send_command(0x9E)
                self.send_data(fan)
                temperature = self.read_data()
                self.read_data()
                duty = self.read_data()
                return temperature, round(duty * 100 / 255)
            except TimeoutError:
                if attempt == 2:
                    raise
                time.sleep(0.02)

    def read_rpm(self, fan):
        offset = (fan - 1) * 2
        value = (self.read_register(0xD0 + offset) << 8) | self.read_register(0xD1 + offset)
        return 0 if value == 0 else round(2156220 / value)


def main():
    ec = EmbeddedController()
    if not ec.driver_open:
        raise RuntimeError("InpOutx64 driver could not be opened")

    result = {"initial_status": f"0x{ec.read_port(EC_COMMAND):02X}"}
    fan_count = ec.read_register(0xC8)
    result["fan_count"] = fan_count
    if not 1 <= fan_count <= 4:
        raise RuntimeError(f"Unexpected fan count: {fan_count}; stopping probe")

    fans = []
    for fan in range(1, fan_count + 1):
        temperature, duty = ec.read_temperature_and_duty(fan)
        fans.append({
            "fan": fan,
            "temperature_c": temperature,
            "duty_percent": duty,
            "rpm": ec.read_rpm(fan),
        })
    result["fans"] = fans
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
