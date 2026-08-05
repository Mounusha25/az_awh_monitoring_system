#!/usr/bin/env python3
"""
Brute-force scanner for the DAE DEM730P (or any Modbus RTU power meter).

Run this when you don't know the meter's address or baud rate:
    python3 scan_powermeter.py

It will:
  1. List every /dev/ttyUSB* port detected on the Pi
  2. Try every combination of baud rate + Modbus address (1-32)
  3. Try both function codes 3 and 4, and two register addresses (0x0000 and 0x0100)
  4. Print exactly which combination gets a response

If NOTHING responds: the RS485 A/B wires are likely swapped — swap them and re-run.

Usage with a specific port:
    python3 scan_powermeter.py /dev/ttyUSB1
"""

import sys
import os
import glob


# ── minimalmodbus import with friendly error ──────────────────────────────────
try:
    import minimalmodbus
except ImportError:
    print("[ERROR] minimalmodbus not installed.")
    print("        Run:  pip3 install minimalmodbus")
    sys.exit(1)


# ── Scan parameters ──────────────────────────────────────────────────────────
BAUD_RATES    = [9600, 2400, 4800, 1200]     # 9600 confirmed for this unit; 2400 was previous meter
ADDRESSES     = list(range(1, 255))          # DEM730P supports addresses 0-254 per manual
FUNC_CODES    = [3, 4]                       # FC3=read holding, FC4=read input
REGISTER_SETS = [0x0000, 0x0100, 0x0001]    # common energy register locations
TIMEOUT_S     = 0.5                          # per attempt — keep short for speed

# Set to True to only scan baud 2400 (DEM730P confirmed default — 4x faster)
FAST_MODE     = True


def detect_ports(explicit_port=None):
    """Return list of serial ports to probe."""
    if explicit_port:
        if not os.path.exists(explicit_port):
            print(f"[!] Specified port {explicit_port} does not exist.")
            sys.exit(1)
        return [explicit_port]

    # Discover all USB serial ports
    ports = sorted(glob.glob("/dev/ttyUSB*"))

    # Also check by-id links (for context — we'll scan the actual ttyUSB device)
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))

    print("=" * 60)
    print("DETECTED SERIAL PORTS")
    print("=" * 60)
    if ports:
        for p in ports:
            print(f"  {p}")
    else:
        print("  (none found — is the USB adapter plugged in?)")

    if by_id:
        print("\nBy-ID symlinks (for reference):")
        for link in by_id:
            try:
                target = os.path.realpath(link)
                print(f"  {os.path.basename(link)}")
                print(f"    -> {target}")
            except Exception:
                print(f"  {link} (broken symlink)")

    print()
    return ports


def try_read(port, baud, address, func_code, register):
    """
    Attempt one Modbus read. Returns raw value on success, None on failure.
    """
    try:
        inst = minimalmodbus.Instrument(port, address)
        inst.serial.baudrate = baud
        inst.serial.bytesize = 8
        inst.serial.parity   = 'N'
        inst.serial.stopbits = 1
        inst.serial.timeout  = TIMEOUT_S
        inst.mode            = minimalmodbus.MODE_RTU
        inst.debug           = False

        # Try reading 2 registers (32-bit value for energy)
        raw = inst.read_long(register, functioncode=func_code, signed=False)
        inst.serial.close()
        return raw

    except minimalmodbus.NoResponseError:
        return None   # meter didn't answer (most common case)
    except minimalmodbus.InvalidResponseError:
        # Got bytes but they're garbled → still a partial hit (wrong baud / address)
        return "GARBLED"
    except Exception:
        return None
    finally:
        try:
            inst.serial.close()
        except Exception:
            pass


def scan(ports):
    """Main scan loop — tries every combination."""
    hits = []
    baud_rates_to_try = [9600] if FAST_MODE else BAUD_RATES
    total = len(ports) * len(baud_rates_to_try) * len(ADDRESSES) * len(FUNC_CODES) * len(REGISTER_SETS)
    est_minutes = round(total * TIMEOUT_S / 60, 1)
    done  = 0

    print("=" * 60)
    print(f"SCANNING  ({total} combinations — est. {est_minutes} min)")
    print(f"Baud rates: {baud_rates_to_try}  {'(fast mode — 2400 only)' if FAST_MODE else ''}")
    print("Addresses: 1-254")
    print("=" * 60)

    baud_rates_to_try = [9600] if FAST_MODE else BAUD_RATES

    for port in ports:
        print(f"\n[Port] {port}")
        for baud in baud_rates_to_try:
            print(f"  [Baud {baud}]", end=" ", flush=True)
            found_at_baud = False
            for addr in ADDRESSES:
                for fc in FUNC_CODES:
                    for reg in REGISTER_SETS:
                        result = try_read(port, baud, addr, fc, reg)
                        done += 1

                        if result is not None:
                            if result == "GARBLED":
                                print(f"\n  [~] Garbled response: port={port} baud={baud} addr={addr} fc={fc} reg=0x{reg:04X}")
                            else:
                                msg = (f"\n  [✓] RESPONSE: port={port}  baud={baud}  "
                                       f"addr={addr}  fc={fc}  reg=0x{reg:04X}  "
                                       f"raw={result}  ({result * 0.01:.2f} kWh if 0.01 scale)")
                                print(msg)
                                hits.append({
                                    "port": port, "baud": baud, "address": addr,
                                    "func_code": fc, "register": reg, "raw": result
                                })
                                found_at_baud = True
                        else:
                            print(".", end="", flush=True)  # progress dot

            if not found_at_baud:
                pass   # dots already printed
        print()  # newline after each port

    return hits


def main():
    explicit = sys.argv[1] if len(sys.argv) > 1 else None
    ports = detect_ports(explicit)

    if not ports:
        print("[ERROR] No serial ports found.")
        print("        1. Check that the USB-RS485 adapter is plugged in")
        print("        2. Run: dmesg | grep ttyUSB")
        sys.exit(1)

    hits = scan(ports)

    print("\n" + "=" * 60)
    if hits:
        print("SCAN COMPLETE — METER FOUND!")
        print("=" * 60)
        for h in hits:
            print(f"  Port     : {h['port']}")
            print(f"  Baud     : {h['baud']}")
            print(f"  Address  : {h['address']}")
            print(f"  Func Code: {h['func_code']}")
            print(f"  Register : 0x{h['register']:04X}")
            print(f"  Raw value: {h['raw']}  ({h['raw'] * 0.01:.2f} kWh if 0.01 scale)")
            print()
        print("Update read_power_new.py with the PORT, ADDRESS above.")
    else:
        print("SCAN COMPLETE — NO METER FOUND")
        print("=" * 60)
        print("Nothing responded on any port/baud/address combination.")
        print()
        print("Next steps:")
        print("  1. SWAP the RS485 A and B wires on the adapter (most common fix)")
        print("     Then re-run this script.")
        print()
        print("  2. Confirm the USB-RS485 adapter is detected:")
        print("     Run: dmesg | grep ttyUSB")
        print()
        print("  3. Confirm the meter is powered on and wired to the adapter:")
        print("     RS485 port on DEM730P: pin A(+) and B(-)")
        print("     USB adapter side:      A(+) and B(-) or D+ and D-")
    print("=" * 60)


if __name__ == "__main__":
    main()
