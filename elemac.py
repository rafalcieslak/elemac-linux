#!/usr/bin/env python3


import sys
import usb.core

dev = usb.core.find(idVendor=0x04d8, idProduct=0x003f)
if dev is None:
    sys.exit("No ELEMAC found in the system")
dev.reset()

try:
    if dev.is_kernel_driver_active(0) is True:
        dev.detach_kernel_driver(0)
except usb.core.USBError as e:
    sys.exit("Kernel driver won't give up control over device: %s" % str(e))

print(" ------- Device")
print(dev)

configuration = dev[0]
configuration.set()

endpoints = configuration[(0,0)]

# TODO: Ensure these are not in a reverse order.
endpoint_in = endpoints[0]
endpoint_out = endpoints[1]
print(" ------- Endpoint IN")
print(endpoint_in)
print(" ------- Endpoint OUT")
print(endpoint_out)

print("\n\n")

message = "v"

print("Sending message: '" + message + "'")


dev.write(endpoint_out.bEndpointAddress, message.encode('ascii') + b'\0')

data = dev.read(endpoint_in.bEndpointAddress,
                endpoint_in.wMaxPacketSize,
                timeout=1000)
response = data.tobytes().split(b'\0')[0].decode('ascii')
print("Received response: '" + response + "'")





usb.util.dispose_resources(dev)
