#!/usr/bin/env python3

# Unfortunately, this file has to target python3.4 (no never version is
# available for Debian 8), and thus type annotations are incomplete.

import os
import sys
import yaml
import json
import smtplib
import argparse
import usb.core
import usb.util
import datetime
import traceback

DATA_DIR = '/var/elemac'
LAST_ALERT_TIMESTAMP_FILE = '/var/elemac/last-alert-'
HISTORIC_DATA_FILE = '/var/elemac/data.log'


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
        self.measurements = {}  # type: ignore
        self.available_measurements = {}  # type: ignore
        for code, data in self.MEAS_BANKS_DATA.items():
            bank, name, unit, divisor = data
            meas = self.read_meas(bank, divisor)
            self.measurements[code] = meas
            self.measurements[code]['bank'] = bank
            self.measurements[code]['name'] = name
            self.measurements[code]['unit'] = unit
            self.measurements[code]['divisor'] = divisor

            if meas['available']:
                self.available_measurements[code] = self.measurements[code]

        self.available_measurements = sorted(
            self.available_measurements.items(), key=lambda x: x[0])


class ElemacController:
    def __init__(self):
        try:
            with open('/etc/elemac', 'r') as file:
                self.config = yaml.load(file)
        except FileNotFoundError:
            self.config = {}

        # Ensure the data directory exists.
        os.makedirs(DATA_DIR, exist_ok=True)

        # TODO: At the moment, only single device connection is supported.
        dev = usb.core.find(idVendor=0x04d8, idProduct=0x003f)
        if dev is None:
            sys.exit("No ELEMAC is connected via USB.")

        self.elemac = ElemacDevice(dev)

        if not self.elemac.connect():
            sys.exit("Failed to connect with ELEMAC.")

    # Returns True if an alert should be suppressed.
    def check_dedup_suppression(self, dedup_channel) -> bool:
        if not dedup_channel:
            return False  # Dedup disabled
        if 'alert_dedup_suppression_hours' not in self.config:
            return False
        hours = self.config['alert_dedup_suppression_hours']

        try:
            with open(LAST_ALERT_TIMESTAMP_FILE + dedup_channel, 'r') as file:
                str = file.readline()
            last_alert = datetime.datetime.strptime(
                str, "%Y-%m-%dT%H:%M:%S.%f")
            delta = datetime.datetime.now() - last_alert
            if delta < datetime.timedelta(hours=hours):
                print("Alert suppressed. Last alert was sent {} ago, "
                      "which is less than {} hours configured.".format(
                          delta, hours))
                return True
            return False
        except FileNotFoundError:
            return False  # No dedup data saved.
        except ValueError:
            return False  # Last timestamp saved in a wrong format.

    def save_dedup_suppression_state(self, dedup_channel):
        if not dedup_channel:
            return
        with open(LAST_ALERT_TIMESTAMP_FILE + dedup_channel, 'w') as file:
            file.write(datetime.datetime.now().isoformat())
        os.chmod(LAST_ALERT_TIMESTAMP_FILE + dedup_channel, 0o666)

    def send_alerts(self, summary, details, brief=None,
                    dedup_channel='other'):
        if not brief:
            brief = summary

        if self.check_dedup_suppression(dedup_channel):
            return

        if dedup_channel and 'alert_dedup_suppression_hours' in self.config:
            details += ("\nNote that due to alert deduplication subsequent "
                        "alerts will be suppressed for the next {} hours."
                        .format(self.config['alert_dedup_suppression_hours']))

        self.send_email_alert(summary, details)
        self.send_sms_alert(brief)

        self.save_dedup_suppression_state(dedup_channel)

    def send_email_alert(self, summary, details):
        email_host = self.config.get('email_host', None)
        if not email_host:
            print("Not sending email, email_host is not configured")
            return
        email_port = self.config.get('email_port', 465)
        email_user = self.config.get('email_user', None)
        if not email_user:
            print("Not sending email, email_user is not configured")
            return
        email_password = self.config.get('email_password', None)
        if not email_password:
            print("Not sending email, email_password is not configured")
            return
        email_from = self.config.get('email_from', email_user)
        email_to = self.config.get('email_to')
        if not email_to:
            print("Not sending email, email_to is not configured")
            return
        email_to_list = [e.strip() for e in email_to.split(',')]

        email_message = "From: {}\nTo: {}\nSubject: {}\n\n{}".format(
            email_from, email_to, summary, details)

        try:
            # Non-SSL connections are not supported.
            server_ssl = smtplib.SMTP_SSL(email_host, email_port)
            server_ssl.login(email_user, email_password)
            server_ssl.sendmail(email_from, email_to_list, email_message)
            server_ssl.close()
            print("Email alert sent.")
        except Exception as e:
            traceback.print_exception(*sys.exc_info())

    def send_sms_alert(self, message):
        # Not implemented yet.
        pass

    # ========= Commands =========

    def show_all(self, args):
        self.elemac.update_all_measurements()

        for code, meas in self.elemac.available_measurements:
            print("{}:".format(meas['name']))

            for field in ['measured', 'hysteresis',
                          'day_high', 'day_low',
                          'night_high', 'night_low',
                          'alarm_high', 'alarm_low']:
                field_name = self.elemac.MEAS_FIELD_DATA[field][3]
                print("    {}: {}{}".format(
                    field_name, meas[field], meas['unit']))

    def show_basic(self, args):
        self.elemac.update_all_measurements()

        for code, meas in self.elemac.available_measurements:
            print("{}: {}{}".format(
                meas['name'], meas['measured'], meas['unit']))

    def check_alarms(self, args):
        self.elemac.update_all_measurements()

        for code, meas in self.elemac.available_measurements:
            if meas['measured'] > meas['alarm_high']:
                summary = "ALARM! {} is too high ({})".format(
                    meas['name'], meas['measured'])
                details = ("Measured value of {} ({}) is above alarm_high "
                           "threshold ({}).".format(
                               meas['name'], meas['measured'],
                               meas['alarm_high']))
                print(summary)
                print(details)
                self.send_alerts(summary, details, dedup_channel=code)

            if meas['measured'] < meas['alarm_low']:
                summary = "ALARM! {} is too low ({})".format(
                    meas['name'], meas['measured'])
                details = ("Measured value of {} ({}) is below alarm_low "
                           "threshold ({}).".format(
                               meas['name'], meas['measured'],
                               meas['alarm_low']))
                print(summary)
                print(details)
                self.send_alerts(summary, details, dedup_channel=code)

    def test_alerts(self, args):
        self.send_alerts(
            "Elemac fishtank test alert",
            "If you see this message, then alert delivery is working",
            dedup_channel=None)

    def store_chart_data(self, args):
        self.elemac.update_all_measurements()

        data = {
            'timestamp': datetime.datetime.now().replace(
                microsecond=0).isoformat(sep=' ')
        }
        for code, meas in self.elemac.available_measurements:
            data[code] = meas['measured']

        with open(HISTORIC_DATA_FILE, 'a') as file:
            file.write(json.dumps(data, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog='elemac')
    subparsers = parser.add_subparsers(dest='command')

    parser_show = subparsers.add_parser(
        'show', help='Print out measurements and configuration.')

    subparsers_show = parser_show.add_subparsers(dest='show_command')

    subparsers_show.add_parser(
        'basic', help='Print out just measured values.')
    subparsers_show.add_parser(
        'all', help='Print out all measurements and configuration values.')

    subparsers.add_parser(
        'check_alarms', help='Check all measurements for alarm state and '
        'send notifications, if configured.')
    subparsers.add_parser(
        'test_alerts', help='Sends a dummy alert to test delivery.')
    subparsers.add_parser(
        'store_chart_data', help='Call this periodically to gather chart '
        'data into a file.')

    args = parser.parse_args()

    controller = ElemacController()

    # EC = ElemacController
    # func = {
    #     None: EC.show_basic,
    #     'show': {
    #         None: EC.show_basic,
    #         'basic': EC.show_basic,
    #         'all': EC.show_all
    #     }
    # }

    if not args.command:
        controller.show_basic(args)
    elif args.command == 'show':
        if args.show_command == 'basic':
            controller.show_basic(args)
        elif args.show_command == 'all':
            controller.show_all(args)
    elif args.command == 'check_alarms':
        controller.check_alarms(args)
    elif args.command == 'test_alerts':
        controller.test_alerts(args)
    elif args.command == 'store_chart_data':
        controller.store_chart_data(args)


if __name__ == "__main__":
    main()
