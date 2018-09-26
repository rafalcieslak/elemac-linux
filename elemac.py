#!/usr/bin/env python3

import sys
import usb.core
import usb.util


# At the moment, only USB connection is supported.
class ElemacDevice:
    SUPPORTED_PRODUCTS = ['SA-03 ver. 1.90']

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
            if dev.is_kernel_driver_active(0) is True:
                dev.detach_kernel_driver(0)
        except usb.core.USBError as e:
            print("Unable to detach kernel driver: " + str(e))
            return False

        self.dev.reset()

        configuration = dev[0]
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


# TODO: At the moment, only single device connection is supported.
dev = usb.core.find(idVendor=0x04d8, idProduct=0x003f)
if dev is None:
    sys.exit("No ELEMAC is connected via USB.")

elemac = ElemacDevice(dev)

connected = elemac.connect()
if not connected:
    sys.exit("Failed to connect with ELEMAC.")

VALUE_BASE = 1864
bank = 0

temp_meas = elemac.read_ram(VALUE_BASE + 17*bank + 2, 2)/10

print("Current temperature: {}".format(temp_meas))
