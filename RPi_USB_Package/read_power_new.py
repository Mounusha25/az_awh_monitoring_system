# read_power_new.py — DAE DEM730P via RS485 Modbus RTU
# Official DAE register map: only Total Energy available via RS485
# Reference: DEM Modbus Reference Basic 1.4e (daecontrol.com)
# Hardware: USB-RS485 adapter → Pi /dev/ttyUSB4
# Install: pip3 install minimalmodbus

import minimalmodbus

PORT    = '/dev/ttyUSB4'   # change if needed (run: ls /dev/ttyUSB*)
ADDRESS = 1                # default meter address (shown on LCD at boot)

_instrument = None

def _connect():
    """Initialize the RS485 connection to the DEM730P meter."""
    inst = minimalmodbus.Instrument(PORT, ADDRESS)
    inst.serial.baudrate = 9600
    inst.serial.bytesize = 8
    inst.serial.parity   = 'N'
    inst.serial.stopbits = 1
    inst.serial.timeout  = 1
    inst.mode            = minimalmodbus.MODE_RTU
    return inst

def read_power():
    """
    Read total energy consumption from DEM730P meter via RS485.

    Returns:
        dict: Power meter data with keys:
            - energy (float): Total energy in kWh (cumulative)
            - power (None): Not available via RS485 on this meter
            - voltage (None): Not available via RS485 on this meter
            - current (None): Not available via RS485 on this meter

        Returns all None values on communication error.
    """
    global _instrument
    try:
        if _instrument is None:
            _instrument = _connect()

        # Official register: address 0x0000, function code 3, 2 words
        # Returns integer — multiply by 0.01 to get kWh
        raw = _instrument.read_long(
            0x0000,
            functioncode=3,
            signed=False
        )
        energy_kwh = round(raw * 0.01, 2)

        return {
            'energy':       energy_kwh,  # kWh cumulative
            'power':        None,        # not available on this meter via RS485
            'voltage':      None,        # not available on this meter via RS485
            'current':      None         # not available on this meter via RS485
        }

    except Exception as e:
        print(f"[Power Meter Error] {e}")
        _instrument = None
        return {
            'energy':  None,
            'power':   None,
            'voltage': None,
            'current': None
        }


if __name__ == "__main__":
    # Quick test when run directly
    print("Testing DEM730P power meter via RS485...")
    print("Make sure USB-RS485 adapter is connected to /dev/ttyUSB4")
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
