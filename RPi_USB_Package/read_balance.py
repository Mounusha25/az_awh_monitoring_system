# read_balance.py
import os
import time
import threading
from typing import Optional, Callable, Tuple, Any

import serial


class BalanceSerialReader:
    """
    Reads balance lines from a serial port and pushes raw lines to a callback.

    Fixes:
    - Avoid hard-coding the changing by-id suffix (0000000X).
    - Auto-detect Prolific/Dtech USB-Serial adapter from /dev/serial/by-id.
    - Reconnect on unplug/replug.
    """

    BY_ID_DIR = "/dev/serial/by-id"
    # Your environment shows these two patterns:
    # - usb-Prolific_Technology_Inc._Dtech_USB-Serial_Controller_...
    # - usb-Prolific_Technology_Inc._USB-Serial_Controller_D-...
    PROLIFIC_MATCH_SUBSTRINGS = (
        "usb-Prolific_Technology_Inc.",
        "USB-Serial_Controller",
    )

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 9600,
        interval: int = 10,
        callback: Optional[Callable[[str], None]] = None,
        timeout: float = 0.1,
        reconnect_delay: float = 1.0,
    ):
        # If port is provided, use it; otherwise auto-detect.
        self.port = port
        self.baudrate = int(baudrate)
        self.timeout = float(timeout)
        self.interval = int(interval)
        self.callback = callback  # receives raw line str
        self.reconnect_delay = float(reconnect_delay)

        self._ser: Optional[serial.Serial] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _is_by_id_path(p: str) -> bool:
        return p.startswith("/dev/serial/by-id/")

    def _find_balance_port(self) -> Optional[str]:
        """
        Returns a stable by-id symlink path for the Prolific adapter if found, else None.
        Chooses the "best" match deterministically.
        """
        if not os.path.isdir(self.BY_ID_DIR):
            return None

        try:
            entries = sorted(os.listdir(self.BY_ID_DIR))
        except Exception:
            return None

        candidates = []
        for name in entries:
            full = os.path.join(self.BY_ID_DIR, name)
            if not os.path.islink(full):
                continue
            # match required substrings
            if all(s in name for s in self.PROLIFIC_MATCH_SUBSTRINGS):
                candidates.append(full)

        if not candidates:
            return None

        # Prefer the Dtech-named one if present, otherwise first sorted
        dtech = [c for c in candidates if "Dtech" in os.path.basename(c)]
        return (sorted(dtech) or candidates)[0]

    def _resolve_port(self) -> Optional[str]:
        """
        Decide which port to use right now.
        """
        if self.port:
            # user-specified port
            if os.path.exists(self.port):
                return self.port
            return None

        auto = self._find_balance_port()
        if auto and os.path.exists(auto):
            return auto
        return None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._close()

    def _close(self):
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass
        finally:
            self._ser = None

    def _open(self, port: str):
        self._ser = serial.Serial(
            port=port,
            baudrate=self.baudrate,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=self.timeout,
        )
        print(f"[Balance] Opened {port} at {self.baudrate}")

    def _run(self):
        last_emit = 0.0
        current_port: Optional[str] = None

        while self._running:
            # Ensure we have an open serial port
            if not self._ser or not self._ser.is_open:
                self._close()

                port = self._resolve_port()
                if not port:
                    # Device not present yet; keep trying
                    if current_port is not None:
                        print("[Balance] Port disappeared; waiting for reconnect...")
                        current_port = None
                    time.sleep(self.reconnect_delay)
                    continue

                try:
                    self._open(port)
                    current_port = port
                except Exception as e:
                    print(f"[Balance] Serial open error on {port}: {e}")
                    self._close()
                    time.sleep(self.reconnect_delay)
                    continue

            # Read loop
            try:
                if self._ser.in_waiting > 0:
                    line = self._ser.readline().decode("utf-8", errors="ignore").strip()
                    if line:
                        now = time.time()
                        if now - last_emit >= self.interval:
                            if self.callback:
                                self.callback(line)
                            last_emit = now
                else:
                    time.sleep(0.01)

            except (serial.SerialException, OSError) as e:
                # unplug/replug or tty gone
                print(f"[Balance] Serial disconnected/read error: {e}")
                self._close()
                time.sleep(self.reconnect_delay)

            except Exception as e:
                print(f"[Balance] Read error: {e}")
                time.sleep(0.2)

        # shutdown
        self._close()
        if current_port:
            print(f"[Balance] Closed {current_port}")


def parse_balance_line(data: str):
    """
    Parses formats:
      - 'ST,GS,+ 0.0000kg'
      - 'S,+    1.5 g'
      - 'ST,GS,OK 123.45 g'
    Returns tuple: (ST, GS, check, weight:float, unit:str) or None if bad.
    """
    try:
        data_norm = " ".join(data.split())

        # Case 1: 'ST,GS,+ 0.0000kg'
        if data_norm.startswith("ST,GS,"):
            parts = data_norm.split(",")
            if len(parts) >= 3:
                ST = parts[0].strip()
                GS = parts[1].strip()
                rest = parts[2].strip()  # e.g. "+ 0.0000kg"
                comps = rest.split()
                if len(comps) == 2:
                    sign = comps[0]  # '+' or '-'
                    weight_str = comps[1]  # '0.0000kg'
                    num = "".join(ch for ch in weight_str if (ch.isdigit() or ch == "." or ch == "-"))
                    unit = weight_str.replace(num, "")
                    weight = float(num)
                    return ST, GS, sign, weight, unit

        # Case 2: 'ST,GS,OK 123.45 g'
        if "," in data_norm and data_norm.count(",") >= 2:
            parts = data_norm.split(",")
            ST = parts[0].strip()
            GS = parts[1].strip()
            weight_part = parts[2].strip()
            comps = weight_part.split()
            if len(comps) == 3:
                check = comps[0].strip()
                weight = float(comps[1])
                unit = comps[2].strip()
                return ST, GS, check, weight, unit

        # Case 3: 'S,+ 1.5 g'
        if data_norm.startswith("S,"):
            left, right = data_norm.split(",", 1)
            ST = left.strip()
            comps = right.strip().split()
            if len(comps) >= 3:
                GS = comps[0]
                check = "OK"
                weight = float(comps[1])
                unit = comps[2]
                return ST, GS, check, weight, unit

        raise ValueError("Unrecognized balance format")

    except Exception as e:
        print(f"[Balance] Parse error: {e} | raw='{data}'")
        return None
