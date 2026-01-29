# read_power.py
import serial
import struct
import time
import threading
import os

class PowerMeterReader:
    """
    Polls a Modbus RTU power meter and pushes (voltage, current, power, energy) via callback.
    Auto-recovers if USB/serial connection is lost.
    """

    DEFAULT_PATH = "/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0"

    def __init__(self, port=None, baudrate=9600, interval=10, callback=None, timeout=2):
        self.port = port or self.DEFAULT_PATH
        if not os.path.exists(self.port):
            raise RuntimeError(f"[Power] Port not found: {self.port}")
        self.baudrate = baudrate
        self.timeout = timeout
        self.interval = int(interval)
        self.callback = callback  # receives tuple (V, A, W, Wh)
        self._ser = None
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass

    def _open(self):
        self._ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        print(f"[Power] Opened {self.port} at {self.baudrate}")

    @staticmethod
    def _crc16(data: bytes) -> int:
        crc = 0xFFFF
        for pos in data:
            crc ^= pos
            for _ in range(8):
                if (crc & 1) != 0:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc

    def _poll_once(self):
        # Build request: slave=0x01, fn=0x04, start=0x0000, count=0x000A
        slave_address = 0x01
        function_code = 0x04
        start_address = 0x0000
        register_count = 0x000A
        req_wo_crc = struct.pack('>BBHH', slave_address, function_code, start_address, register_count)
        crc = self._crc16(req_wo_crc)
        request = req_wo_crc + struct.pack('<H', crc)

        # send + read
        self._ser.write(request)
        response = self._ser.read(25)
        if len(response) != 25:
            print("[Power] Incomplete response")
            return None

        # parse
        try:
            voltage_raw = struct.unpack('>H', response[3:5])[0]
            current_low_raw = struct.unpack('>H', response[5:7])[0]
            current_high_raw = struct.unpack('>H', response[7:9])[0]
            power_low_raw = struct.unpack('>H', response[9:11])[0]
            power_high_raw = struct.unpack('>H', response[11:13])[0]
            energy_raw = struct.unpack('>H', response[13:15])[0]

            voltage = round(voltage_raw * 0.1, 3)
            current = round((current_high_raw * 65536 + current_low_raw) * 0.001, 3)
            power = round((power_high_raw * 65536 + power_low_raw) * 0.1, 3)
            energy = round(energy_raw * 1, 3)
            return voltage, current, power, energy
        except Exception as e:
            print(f"[Power] Parse error: {e}")
            return None

    def _run(self):
        while self._running:
            try:
                if not (self._ser and self._ser.is_open):
                    self._open()

                data = self._poll_once()
                if data and self.callback:
                    self.callback(*data)

            except Exception as e:
                print(f"[Power] Poll error: {e}")
                # Close and reset so next loop reopens
                try:
                    if self._ser and self._ser.is_open:
                        self._ser.close()
                        print("[Power] Port closed after error, will retry")
                except Exception as ce:
                    print(f"[Power] Error closing port: {ce}")
                self._ser = None
                time.sleep(2)  # backoff before retry

            time.sleep(self.interval)

        # cleanup on exit
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
                print(f"[Power] Closed {self.port}")
        except Exception as e:
            print(f"[Power] Close error: {e}")
