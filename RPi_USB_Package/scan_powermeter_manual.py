#!/usr/bin/env python3
"""
Address/baud sweep for a Modbus RTU power meter — no minimalmodbus required.
Uses the same manual pyserial + CRC16 approach as read_power.py / debug_powermeter.py.

Run this when debug_powermeter.py shows "No bytes received" at address 1
on every function code / register combo (i.e. wiring and power are already
confirmed OK, and you want to rule out address/baud mismatch before
escalating a hardware check):

    python3 scan_powermeter_manual.py /dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0

It sweeps addresses 1-32 (plus common factory defaults 100, 247) across
baud rates 9600 / 2400 / 4800 / 1200, trying FC3 and FC4 on register 0x0000.
"""

import sys
import os
import serial
import struct
import time

BAUD_RATES = [9600, 2400, 4800, 1200]
ADDRESSES = list(range(1, 33)) + [100, 247]
FUNC_CODES = [3, 4]
REGISTER = 0x0000
REG_COUNT = 2
TIMEOUT_S = 0.25


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_request(slave, func, reg_addr, reg_count):
    frame = struct.pack('>BBHH', slave, func, reg_addr, reg_count)
    crc = crc16(frame)
    return frame + struct.pack('<H', crc)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scan_powermeter_manual.py <port>")
        print("       e.g. /dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0")
        sys.exit(1)

    port = sys.argv[1]
    if not os.path.exists(port):
        print(f"[ERROR] Port not found: {port}")
        sys.exit(1)

    total = len(BAUD_RATES) * len(ADDRESSES) * len(FUNC_CODES)
    print("=" * 60)
    print(f"MANUAL ADDRESS/BAUD SWEEP ({total} combinations)")
    print(f"Port: {port}")
    print("=" * 60)

    hits = []
    done = 0

    for baud in BAUD_RATES:
        ser = serial.Serial(port, baud, bytesize=8, parity='N', stopbits=1, timeout=TIMEOUT_S)
        print(f"\n[Baud {baud}]", end=" ", flush=True)
        for addr in ADDRESSES:
            for fc in FUNC_CODES:
                request = build_request(addr, fc, REGISTER, REG_COUNT)
                ser.reset_input_buffer()
                ser.write(request)
                ser.flush()
                time.sleep(0.05)
                raw = ser.read(ser.in_waiting or 9)
                done += 1

                if raw:
                    print(f"\n  [~] Response: baud={baud} addr={addr} fc={fc} "
                          f"raw={' '.join(f'{b:02X}' for b in raw)} ({len(raw)} bytes)")
                    hits.append((baud, addr, fc, raw))
                else:
                    print(".", end="", flush=True)
        ser.close()
    print()

    print("\n" + "=" * 60)
    if hits:
        print("SWEEP COMPLETE — RESPONSE(S) FOUND")
        print("=" * 60)
        for baud, addr, fc, raw in hits:
            print(f"  baud={baud}  addr={addr}  fc={fc}  bytes={len(raw)}")
        print("\nUpdate read_power.py's baudrate / slave_address to match, then re-test.")
    else:
        print("SWEEP COMPLETE — NOTHING RESPONDED")
        print("=" * 60)
        print("Address and baud are ruled out as the cause (checked 1-32, 100, 247")
        print("across 9600/2400/4800/1200). This points at wiring or power —")
        print("flag it to your supervisor for a physical check:")
        print("  - RS485 A/B wires possibly swapped or disconnected")
        print("  - Meter not receiving power")
        print("  - Adapter may need RTS-toggled half-duplex direction control")
    print("=" * 60)


if __name__ == "__main__":
    main()
