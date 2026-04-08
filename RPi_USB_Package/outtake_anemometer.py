# outtake_anemometer.py
# Safe version with timeout (prevents Tkinter UI from freezing)

import os
import serial
import time

# Use udev symlink for stable device path or USB port
DEFAULT_PORT = "/dev/ttyUSB1"

def outtake_anemometer(serial_port: str = DEFAULT_PORT, baud_rate: int = 9600, timeout: int = 2):
    """
    Read one packet from outtake anemometer and decode
    humidity (%), temperature (°C), velocity, and unit.
    If timeout expires, return (None, None, None, "m/s").
    """
    if not os.path.exists(serial_port):
        print(f"[Outtake Anemometer] Serial port not found: {serial_port}")
        return None, None, None, "m/s"

    ser = serial.Serial(serial_port, baud_rate, timeout=0.2)
    buffer = b''
    start_time = time.time()

    def decode_velocity(b):
        v1 = (b[0] << 8) + b[1]
        v2 = (b[2] << 8) + b[3]
        v3 = (b[4] << 8) + b[5]
        avg_value = (v1 + v2 + v3) / 3
        scale_factor = 1.53 / ((0x98 * 3) / 3)
        return round(avg_value * scale_factor - 0.01, 2)

    def determine_unit_and_conversion(byte3, byte4):
        # Accept intake modes (0x00, 0x08) and outtake mode (0x10) as velocity
        if byte3 in (0x00, 0x08, 0x10):
            return {
                0x00: ("m/s", 1),
                0x04: ("km/h", 3.6),
                0x08: ("MPH", 2.23694),
                0x0C: ("ft/m", 196.850394),
                0x10: ("ft/s", 3.28084),
            }.get(byte4, ("m/s", 1))  # fallback = m/s
        return "Unknown Unit", 1

    def decode_temperature(b):
        raw = (b[0] << 8) + b[1]
        return round((raw - 0x0238) * (27.3 - 26.8) / (0x023d - 0x0238) + 26.8, 1)

    def decode_humidity(b):
        raw = (b[0] << 8) + b[1]
        return round((raw - 0x0141) * (32.3 - 32.1) / (0x0143 - 0x0141) + 32.1, 1)

    def process_packet(packet):
        byte3, byte4 = packet[2], packet[3]
        unit, _ = determine_unit_and_conversion(byte3, byte4)
        h = decode_humidity(packet[4:6])
        t = decode_temperature(packet[6:8])
        v = decode_velocity(packet[8:14])
        print(f"[Outtake] Humidity={h:.1f} %, Temp={t:.1f} °C, Velocity={v:.2f} {unit}")
        return h, t, v, unit

    def find_start(buf):
        start = buf.find(b'\xeb\xa0')
        return start if (start != -1 and len(buf[start:]) >= 16) else -1

    try:
        while True:
            if time.time() - start_time > timeout:
                print("[Outtake] Timeout - no packet received")
                return None, None, None, "m/s"

            buffer += ser.read(ser.in_waiting or 1)
            start = find_start(buffer)
            if start != -1 and len(buffer[start:]) >= 16:
                pkt = buffer[start:start + 16]
                return process_packet(pkt)
    finally:
        ser.close()
