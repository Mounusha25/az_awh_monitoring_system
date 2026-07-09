# intake_anemometer.py
import os
import serial
import time

# Both anemometers use identical Silicon Labs CP2102 chips, so by-id names
# can't be told apart by content alone (unlike the power meter's unique FTDI
# adapter or the balance's unique Prolific adapter). We list all CP2102
# by-id entries (sorted, so order is stable across runs) and assign this one
# index 0 (intake) / outtake_anemometer.py takes index 1.
# ttyUSBx numbers are NOT used directly because they get reassigned by
# plug-in order at every boot.
#
# IMPORTANT: verify this assignment against the physical sensors after any
# reboot/replug — if intake/outtake readings look swapped, flip
# ANEMOMETER_INDEX to 1 here (and to 0 in outtake_anemometer.py).
BY_ID_DIR = "/dev/serial/by-id"
CP210X_MATCH_SUBSTRING = "CP2102"
ANEMOMETER_INDEX = 0
DEFAULT_PORT = "/dev/ttyUSB2"  # fallback only, used if by-id lookup fails


def find_anemometer_ports():
    if not os.path.isdir(BY_ID_DIR):
        return []
    try:
        entries = sorted(os.listdir(BY_ID_DIR))
    except OSError:
        return []
    return [os.path.join(BY_ID_DIR, name) for name in entries if CP210X_MATCH_SUBSTRING in name]


def find_intake_port():
    candidates = find_anemometer_ports()
    if len(candidates) > ANEMOMETER_INDEX:
        port = candidates[ANEMOMETER_INDEX]
        print(f"[Intake] Auto-detected port: {port} (of {len(candidates)} CP2102 adapters found)")
        return port
    print(f"[Intake] Could not auto-detect via by-id ({len(candidates)} CP2102 adapters found), "
          f"falling back to {DEFAULT_PORT}")
    return DEFAULT_PORT


def intake_anemometer(serial_port: str = None, baud_rate: int = 9600, timeout: int = 2):
    """อ่าน 1 packet จาก intake anemometer และ decode ค่า (humidity, temperature, velocity, unit)."""
    if serial_port is None:
        serial_port = find_intake_port()
    if not os.path.exists(serial_port):
        raise FileNotFoundError(f"[Intake Anemometer] Serial port not found: {serial_port}")

    ser = serial.Serial(serial_port, baud_rate, timeout=0.2)
    buffer = b''
    start_time = time.time()

    def decode_velocity(bytes_9_to_14):
        v1 = (bytes_9_to_14[0] << 8) + bytes_9_to_14[1]
        v2 = (bytes_9_to_14[2] << 8) + bytes_9_to_14[3]
        v3 = (bytes_9_to_14[4] << 8) + bytes_9_to_14[5]
        avg_value = (v1 + v2 + v3) / 3
        scale_factor = 1.53 / ((0x98 * 3) / 3)
        return round(avg_value * scale_factor - 0.01, 2)

    def determine_unit_and_conversion(byte3, byte4):
        if byte3 == 0x00:  # velocity mode
            return {
                0x00: ("m/s", 1),
                0x04: ("km/h", 3.6),
                0x08: ("MPH", 2.23694),
                0x0C: ("ft/m", 196.850394),
                0x10: ("ft/s", 3.28084),
            }.get(byte4, ("Unknown Unit", 1))
        return "Unknown Unit", 1

    def decode_temperature(b):
        raw = (b[0] << 8) + b[1]
        return round((raw - 0x0238) * (27.3 - 26.8) / (0x023d - 0x0238) + 26.8, 1)

    def decode_humidity(b):
        raw = (b[0] << 8) + b[1]
        return round((raw - 0x0141) * (32.3 - 32.1) / (0x0143 - 0x0141) + 32.1, 1)

    def process_packet(packet):
        unit, _ = determine_unit_and_conversion(packet[2], packet[3])
        h = decode_humidity(packet[4:6])
        t = decode_temperature(packet[6:8])
        v = decode_velocity(packet[8:14])
        print(f"[Intake] Humidity={h:.1f} %, Temp={t:.1f} °C, Velocity={v:.2f} {unit}")
        return h, t, v, unit

    def find_start(buf):
        start = buf.find(b'\xeb\xa0')
        return start if (start != -1 and len(buf[start:]) >= 16) else -1

    try:
        while True:
            if time.time() - start_time > timeout:
                print("[Intake] Timeout - no packet received")
                return None, None, None, "m/s"

            buffer += ser.read(ser.in_waiting or 1)
            start = find_start(buffer)
            if start != -1:
                if len(buffer[start:]) >= 16:
                    pkt = buffer[start:start+16]
                    h, t, v, unit = process_packet(pkt)
                    buffer = buffer[start+16:]
                    return h, t, v, unit
    finally:
        ser.close()
