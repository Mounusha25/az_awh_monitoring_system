# read_power_new.py — DAE DEM730P via RS485 Modbus RTU
# Official DAE register map: only Total Energy available via RS485
# Reference: DEM Modbus Reference Basic 1.4e (daecontrol.com)
# Hardware: USB-RS485 adapter → Pi /dev/ttyUSBx
# Install: pip3 install minimalmodbus
#
# DEM730P defaults (from installation guide):
#   Baud rate : 2400  (NOT 9600 — this is the most common wiring mistake)
#   Address   : 1
#   Baud options: 1200, 2400, 4800, 9600

import minimalmodbus
import time
import threading
import os

# Stable symlink that survives reboots regardless of plug-in order.
# Find yours with: ls /dev/serial/by-id/
# Falls back to /dev/ttyUSB1 if symlink is not present.
DEFAULT_PORT = "/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0"
PORT    = DEFAULT_PORT if os.path.exists(DEFAULT_PORT) else '/dev/ttyUSB1'
ADDRESS = 1                # default meter address (shown on LCD at boot — A1)


class PowerMeterReader:
    """
    Threaded reader for DEM730P power meter via RS485 Modbus RTU.
    Polls the meter periodically and invokes a callback with (voltage, current, power, energy).
    
    Note: DEM730P via RS485 can only read energy; returns (None, None, None, energy).
    """

    def __init__(self, port=None, baudrate=2400, address=1, interval=10, callback=None, timeout=2):
        """
        Initialize the power meter reader.

        Args:
            port: Serial port (default: by-id symlink, fallback /dev/ttyUSB1)
            baudrate: Baud rate (default: 2400 — DEM730P factory default per installation guide)
            address: Modbus address (default: 1)
            interval: Poll interval in seconds (default: 10)
            callback: Function to call with (voltage, current, power, energy)
                      DEM730P via RS485 only exposes energy → (None, None, None, energy_kwh)
            timeout: Serial timeout in seconds (default: 2)
        """
        self.port = port or PORT
        if not os.path.exists(self.port):
            raise RuntimeError(f"[Power] Port not found: {self.port} — run: ls /dev/serial/by-id/ or ls /dev/ttyUSB*")
        self.baudrate = baudrate
        self.address = address
        self.interval = int(interval)
        self.callback = callback
        self.timeout = timeout
        self._instrument = None
        self._running = False
        self._thread = None

    def start(self):
        """Start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[Power] Started polling on {self.port}")

    def stop(self):
        """Stop the background polling thread."""
        self._running = False
        try:
            if self._instrument:
                self._instrument.serial.close()
        except Exception as e:
            print(f"[Power] Error closing connection: {e}")
        self._instrument = None
        print("[Power] Stopped")

    def _connect(self):
        """Initialize the RS485 connection to the DEM730P meter."""
        try:
            inst = minimalmodbus.Instrument(self.port, self.address)
            inst.serial.baudrate = self.baudrate  # 2400 default for DEM730P
            inst.serial.bytesize = 8
            inst.serial.parity   = 'N'
            inst.serial.stopbits = 1
            inst.serial.timeout  = self.timeout
            inst.mode            = minimalmodbus.MODE_RTU
            print(f"[Power] Connected to {self.port} @ {self.baudrate} baud, address {self.address}")
            return inst
        except Exception as e:
            print(f"[Power] Connection failed: {e}")
            return None

    def _run(self):
        """Background polling loop."""
        while self._running:
            try:
                if self._instrument is None:
                    self._instrument = self._connect()
                    if self._instrument is None:
                        time.sleep(2)  # backoff before retry
                        continue

                # Read total energy from register 0x0000 (2 words, function code 3)
                # Returns integer — multiply by 0.01 to get kWh
                raw = self._instrument.read_long(
                    0x0000,
                    functioncode=3,
                    signed=False
                )
                energy_kwh = round(raw * 0.01, 2)

                # Invoke callback: (voltage, current, power, energy)
                # DEM730P via RS485 can only read energy, so others are None
                if self.callback:
                    self.callback(None, None, None, energy_kwh)

            except Exception as e:
                print(f"[Power] Poll error: {e}")
                try:
                    self._instrument.serial.close()  # release port before retry
                except Exception:
                    pass
                self._instrument = None
                time.sleep(2)  # backoff before retry
                continue

            time.sleep(self.interval)

        # cleanup on exit
        try:
            if self._instrument:
                self._instrument.serial.close()
                print("[Power] Closed connection")
        except Exception as e:
            print(f"[Power] Error during cleanup: {e}")


def read_power():
    """
    Read total energy consumption from DEM730P meter via RS485 (synchronous, single read).

    Returns:
        dict: Power meter data with keys:
            - energy (float): Total energy in kWh (cumulative)
            - power (None): Not available via RS485 on this meter
            - voltage (None): Not available via RS485 on this meter
            - current (None): Not available via RS485 on this meter

        Returns all None values on communication error.
    """
    try:
        inst = minimalmodbus.Instrument(PORT, ADDRESS)
        inst.serial.baudrate = 2400  # DEM730P factory default
        inst.serial.bytesize = 8
        inst.serial.parity   = 'N'
        inst.serial.stopbits = 1
        inst.serial.timeout  = 1
        inst.mode            = minimalmodbus.MODE_RTU

        # Official register: address 0x0000, function code 3, 2 words
        # Returns integer — multiply by 0.01 to get kWh
        raw = inst.read_long(
            0x0000,
            functioncode=3,
            signed=False
        )
        energy_kwh = round(raw * 0.01, 2)
        inst.serial.close()

        return {
            'energy':       energy_kwh,  # kWh cumulative
            'power':        None,        # not available on this meter via RS485
            'voltage':      None,        # not available on this meter via RS485
            'current':      None         # not available on this meter via RS485
        }

    except Exception as e:
        print(f"[Power Meter Error] {e}")
        return {
            'energy':  None,
            'power':   None,
            'voltage': None,
            'current': None
        }


if __name__ == "__main__":
    # Quick test when run directly
    print("Testing DEM730P power meter via RS485...")
    print(f"Using port: {PORT}")
    print("To find your port: ls /dev/serial/by-id/  or  ls /dev/ttyUSB*")
    print("-" * 50)

    data = read_power()

    if data['energy'] is not None:
        print("✓ SUCCESS")
        print(f"  Energy: {data['energy']} kWh")
    else:
        print("✗ FAILED - check connection and wiring")
        print("  Debug steps:")
        print("  1. Run: ls /dev/ttyUSB* (verify adapter is detected)")
        print("  2. Check RS485 wiring: A+ to A, B- to B")
        print("  3. Verify meter address (shown on LCD at boot)")
