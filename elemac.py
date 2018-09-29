#!/usr/bin/env python3

# Unfortunately, this file has to target python3.4 (no never version is
# available for Debian 8), and thus type annotations are incomplete.

import sys
import usb.core
import usb.util


# At the moment, only USB connection is supported.
class ElemacDevice:
    SUPPORTED_PRODUCTS = ['SA-03 ver. 1.90']

    MEAS_BASE = 1864
    MEAS_STRUCT_SIZE = 17
    # Values follow format: (offset, size, divisive, name)
    MEAS_FIELD_DATA = {
        'flags': (0, 1, False, "Flags"),
        'type': (1, 1, False, "Type"),
        'measured': (2, 2, True, "Measured"),
        'alarm_high': (4, 2, True, "Alarm High"),
        'day_high': (6, 2, True, "Day High"),
        'night_high': (8, 2, True, "Night High"),
        'hysteresis': (10, 1, True, "Hysteresis"),
        'night_low': (11, 2, True, "Night Low"),
        'day_low': (13, 2, True, "Day Low"),
        'alarm_low': (15, 2, True, "Alarm Low"),
    }
    MEAS_PRINTABLE_FIELDS = [
        'measured', 'hysteresis',
        'day_high', 'day_low',
        'night_high', 'night_low',
        'alarm_high', 'alarm_low'
    ]

    # Values follow format: (bank_no, name, unit, value_divisor)
    MEAS_BANKS_DATA = {
        'temp1': (0, 'Temperature 1', '°C', 10),
        'temp2': (1, 'Temperature 2', '°C', 10),
        'temp3': (2, 'Temperature 3', '°C', 10),
        'temp4': (3, 'Temperature 4', '°C', 10),
        'ph1': (4, 'pH 1', '', 100),
        'ph2': (5, 'pH 2', '', 100),
        'humid': (6, 'Humidity', '%', 10),
        'redox': (7, 'Redox', 'mV', 1),
    }

    def __init__(self, usb_dev: usb.core.Device) -> None:
        self.dev = usb_dev

    def __del__(self):
        pass

    # Initializes connection; returns True iff connection was successful.
    def connect(self):
        self.manufacturer = usb.util.get_string(
            self.dev, self.dev.iManufacturer)
        self.product = usb.util.get_string(
            self.dev, self.dev.iProduct)
        self.serial_no = usb.util.get_string(
            self.dev, self.dev.iSerialNumber)

        if self.manufacturer != 'ELEMAC':
            return False

        if self.product not in self.SUPPORTED_PRODUCTS:
            print("'{}' is not supported.".format(self.product))

        try:
            if self.dev.is_kernel_driver_active(0) is True:
                self.dev.detach_kernel_driver(0)
        except usb.core.USBError as e:
            print("Unable to detach kernel driver: " + str(e))
            return False

        self.dev.reset()

        configuration = self.dev[0]
        configuration.set()

        endpoints = configuration[(0, 0)]

        # TODO: Ensure these never appear in a reverse order.
        self.ep_in = endpoints[0]
        self.ep_out = endpoints[1]

        return True

    def send_command(self, command: str) -> str:
        # print("Sending command: '" + command + "'")

        self.dev.write(self.ep_out.bEndpointAddress,
                       command.encode('ascii') + b'\0')

        data = self.dev.read(self.ep_in.bEndpointAddress,
                             self.ep_in.wMaxPacketSize, timeout=1000)
        response = data.tobytes().split(b'\0')[0].decode('ascii')

        # print("Received response: '" + response + "'")
        return response

    def read_ram(self, address: int, size: int) -> int:
        assert size >= 1 and size <= 4
        size_prefix = str(size - 1)

        cmd = 'r' + size_prefix + '{:3x}'.format(address)
        response = self.send_command(cmd)
        bytes = [response[i:i+2] for i in range(0, len(response), 2)]
        bytes_le = reversed(bytes)
        value = int(''.join(bytes_le), 16)

        return value

    def read_meas(self, bank: int, divisor: int = 1):
        meas = {}
        for value_name, data in self.MEAS_FIELD_DATA.items():
            offset, size, divisive, name = data
            value = self.read_ram(
                self.MEAS_BASE + bank * self.MEAS_STRUCT_SIZE + offset,
                size)
            if divisive:
                meas[value_name] = value/divisor
            else:
                meas[value_name] = value

        meas['available'] = meas['flags'] & 1  # type: ignore

        return meas

    def update_all_measurements(self) -> None:
        self.meas_values = {}  # type: ignore
        for code, data in self.MEAS_BANKS_DATA.items():
            bank, name, unit, divisor = data
            meas = self.read_meas(bank, divisor)
            self.meas_values[code] = meas

    def print_available_measurements(self):
        for code, meas in self.meas_values.items():
            if not meas['available']:
                continue
            _, name, unit, _ = self.MEAS_BANKS_DATA[code]
            print("{}:".format(name))

            for field in self.MEAS_PRINTABLE_FIELDS:
                _, _, _, field_name = self.MEAS_FIELD_DATA[field]
                print("    {}: {}{}".format(
                    field_name, meas[field], unit))


def main() -> None:
    # TODO: At the moment, only single device connection is supported.
    dev = usb.core.find(idVendor=0x04d8, idProduct=0x003f)
    if dev is None:
        sys.exit("No ELEMAC is connected via USB.")

    elemac = ElemacDevice(dev)

    connected = elemac.connect()
    if not connected:
        sys.exit("Failed to connect with ELEMAC.")

    elemac.update_all_measurements()
    elemac.print_available_measurements()


if __name__ == "__main__":
    main()
