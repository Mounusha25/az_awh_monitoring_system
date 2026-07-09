# read_power_new.py — DAE DEM730P via RS485 Modbus RTU
# Official DAE register map: only Total Energy available via RS485
# Reference: DEM Modbus Reference Basic 1.4e (daecontrol.com)
# Hardware: USB-RS485 adapter → Pi /dev/ttyUSBx
# Install: pip3 install minimalmodbus
#
# DEM730P defaults (from installation guide):
#   Baud rate : 9600  (confirmed from manual for this unit)
#   Address   : last 2 digits of serial number (e.g. serial ending 01 → address 1)
#   Baud options: 1200, 2400, 4800, 9600

import minimalmodbus
import time
import threading
import os

BY_ID_DIR = "/dev/serial/by-id"
FTDI_MATCH_SUBSTRING = "FTDI"  # power meter is the only FTDI adapter on this station


def find_power_meter_port():
    """
    Auto-detect the power meter's FTDI USB-RS485 adapter via /dev/serial/by-id.

    /dev/ttyUSBx numbers are reassigned by plug-in order at every boot, so
    hard-coding one (e.g. ttyUSB1) can silently bind to a different sensor
    (balance, anemometer) after a reboot/replug. by-id symlinks are keyed to
    the adapter's USB identity and stay stable regardless of enumeration order.
    Returns None if no FTDI adapter is currently present.
    """
    if not os.path.isdir(BY_ID_DIR):
        return None
    try:
        entries = sorted(os.listdir(BY_ID_DIR))
    except OSError:
        return None
    candidates = [os.path.join(BY_ID_DIR, name) for name in entries if FTDI_MATCH_SUBSTRING in name]
    return candidates[0] if candidates else None


PORT    = find_power_meter_port()
ADDRESS = 1                # last 2 digits of serial number — new meter serial ends in 01


class PowerMeterReader:
    """
    Threaded reader for DEM730P power meter via RS485 Modbus RTU.
    Polls the meter periodically and invokes a callback with (voltage, current, power, energy).
    
    Note: DEM730P via RS485 can only read energy; returns (None, None, None, energy).
    """

    def __init__(self, port=None, baudrate=9600, address=1, interval=10, callback=None, timeout=2):
        """
        Initialize the power meter reader.

        Args:
            port: Serial port (default: auto-detected FTDI adapter via /dev/serial/by-id)
            baudrate: Baud rate (default: 2400 — DEM730P factory default per installation guide)
            address: Modbus address (default: 99 — confirmed from meter LCD)
            interval: Poll interval in seconds (default: 10)
            callback: Function to call with (voltage, current, power, energy)
                      DEM730P via RS485 only exposes energy → (None, None, None, energy_kwh)
            timeout: Serial timeout in seconds (default: 2)
        """
        self.port = port or PORT or find_power_meter_port()
        if not self.port or not os.path.exists(self.port):
            raise RuntimeError(
                "[Power] FTDI power meter adapter not found under /dev/serial/by-id — "
                "check the USB cable is connected and run: ls /dev/serial/by-id/"
            )
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

            # Enable RS485 half-duplex RTS direction control.
            # Some USB-RS485 adapters need RTS toggled to switch between
            # transmit and receive. Without this, the adapter keeps its
            # transmitter on after sending, blocking the meter's response.
            try:
                import serial.rs485
                inst.serial.rs485_mode = serial.rs485.RS485Settings(
                    rts_level_for_tx=True,
                    rts_level_for_rx=False,
                    loopback=False,
                    delay_before_tx=0.0,
                    delay_before_rx=0.0,
                )
                print("[Power] RS485 RTS direction control enabled")
            except Exception:
                # Adapter handles direction automatically — no action needed
                print("[Power] RS485 RTS mode not supported by this adapter (auto-direction)")

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
                if self._running:  # suppress error noise from intentional stop
                    print(f"[Power] Poll error: {e}")
                try:
                    if self._instrument:
                        self._instrument.serial.close()
                except Exception:
                    pass
                self._instrument = None
                if not self._running:
                    break  # stop() was called — exit immediately, don't retry
                time.sleep(2)
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
    port = find_power_meter_port()
    if not port:
        print("[Power Meter Error] FTDI adapter not found under /dev/serial/by-id — check cable and run: ls /dev/serial/by-id/")
        return {'energy': None, 'power': None, 'voltage': None, 'current': None}

    try:
        inst = minimalmodbus.Instrument(port, ADDRESS)
        inst.serial.baudrate = 9600  # confirmed baud for this meter unit
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
    print(f"Using port: {find_power_meter_port()}")
    print("To find your port: ls /dev/serial/by-id/")
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
