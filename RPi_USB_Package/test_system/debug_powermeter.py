#!/usr/bin/env python3
"""
debug_powermeter.py
====================
Low-level diagnostic for DEM730P RS485 communication.
Shows every raw byte sent and received so you can see exactly what's happening.

Run this when test_powermeter_new.py gives "No communication":
    python3 debug_powermeter.py

It will tell you:
  - Whether the Modbus request frame is being sent correctly
  - Whether ANY bytes are coming back from the meter
  - If bytes come back but are wrong (wiring issue vs. protocol issue)
"""

import sys
import os
import serial
import struct
import time

# ── Port detection ────────────────────────────────────────────────────────────
DEFAULT_BY_ID = "/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0"
FALLBACK      = "/dev/ttyUSB1"

PORT    = sys.argv[1] if len(sys.argv) > 1 else (DEFAULT_BY_ID if os.path.exists(DEFAULT_BY_ID) else FALLBACK)
BAUD    = 9600
ADDRESS = 1

# ── CRC16 (standard Modbus) ───────────────────────────────────────────────────
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

# ── Build a Modbus RTU read-holding-registers frame ───────────────────────────
def build_request(slave, func, reg_addr, reg_count):
    frame = struct.pack('>BBHH', slave, func, reg_addr, reg_count)
    crc   = crc16(frame)
    return frame + struct.pack('<H', crc)

def print_hex(label, data):
    hex_str = ' '.join(f'{b:02X}' for b in data)
    print(f"  {label}: [{hex_str}]  ({len(data)} bytes)")

# ── Main diagnostic ───────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("DEM730P RS485 RAW BYTE DIAGNOSTIC")
    print("=" * 60)
    print(f"Port    : {PORT}")
    print(f"Baud    : {BAUD}")
    print(f"Address : {ADDRESS}")
    print()

    # Open port
    try:
        ser = serial.Serial(PORT, BAUD, bytesize=8, parity='N',
                            stopbits=1, timeout=2)
        print(f"[OK] Port opened: {PORT}")
    except Exception as e:
        print(f"[FAIL] Cannot open port: {e}")
        print()
        print("This means either:")
        print("  1. Wrong port — run: ls /dev/ttyUSB*")
        print("  2. Port is held by another process — run: lsof /dev/ttyUSB1")
        sys.exit(1)

    time.sleep(0.1)          # brief settle time after open
    ser.reset_input_buffer() # clear any stale bytes

    # Try FC3 and FC4, register 0x0000, 2 registers
    combos = [
        (3, 0x0000, 2, "FC3 reg 0x0000 (2 words) — energy"),
        (4, 0x0000, 2, "FC4 reg 0x0000 (2 words) — energy"),
        (3, 0x0000, 1, "FC3 reg 0x0000 (1 word)"),
        (3, 0x0100, 2, "FC3 reg 0x0100 (2 words)"),
    ]

    for fc, reg, count, label in combos:
        print(f"\nTrying: {label}")
        request = build_request(ADDRESS, fc, reg, count)
        print_hex("  TX (sent)", request)

        ser.reset_input_buffer()
        ser.write(request)
        ser.flush()

        # Wait for response — at 2400 baud a 9-byte response takes ~37ms
        time.sleep(0.3)
        raw = ser.read(ser.in_waiting or 32)

        if raw:
            print_hex("  RX (received)", raw)
            # Basic validation
            if len(raw) >= 5:
                if raw[0] == ADDRESS and raw[1] == fc:
                    byte_count = raw[2]
                    data_bytes = raw[3:3+byte_count]
                    # Parse 32-bit big-endian value
                    if len(data_bytes) >= 4:
                        raw_val = struct.unpack('>I', data_bytes[:4])[0]
                        print(f"  [✓] VALID RESPONSE — raw={raw_val}  "
                              f"energy={raw_val * 0.01:.2f} kWh (if 0.01 scale)")
                    elif len(data_bytes) >= 2:
                        raw_val = struct.unpack('>H', data_bytes[:2])[0]
                        print(f"  [✓] VALID RESPONSE (16-bit) — raw={raw_val}  "
                              f"energy={raw_val * 0.01:.2f} kWh")
                elif raw[0] == ADDRESS and raw[1] == (fc | 0x80):
                    print(f"  [!] MODBUS EXCEPTION — error code: {raw[2]}")
                    print("      Meter responded but rejected the request.")
                    print("      Try a different register address.")
                else:
                    print("  [~] Got bytes but header doesn't match — possible noise or wrong address")
            else:
                print("  [~] Got some bytes but too few for a valid Modbus frame")
                print("      Could be noise, or partial response (baud mismatch?)")
        else:
            print("  [✗] No bytes received — meter did not respond")
            print("      Possible causes:")
            print("        A) A/B wires swapped on RS485 adapter — swap and re-run")
            print("        B) Wrong Modbus address (meter not address 1)")
            print("        C) RS485 adapter needs RTS direction control")

        time.sleep(0.5)

    ser.close()
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("If ALL attempts show [✗] No bytes received:")
    print("  → Swap A/B wires on the USB adapter, then re-run this script")
    print()
    print("If you see [~] Got bytes but header doesn't match:")
    print("  → Wiring is OK, address may be wrong. Try:")
    print("     python3 scan_powermeter.py")
    print()
    print("If you see [✓] VALID RESPONSE:")
    print("  → Communication is working. Run test_powermeter_new.py")


if __name__ == "__main__":
    main()
