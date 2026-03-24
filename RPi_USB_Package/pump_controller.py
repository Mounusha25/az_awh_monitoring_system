# pump_controller.py (gpiozero version — works on RPi 4 and RPi 5)
import threading
import time
from gpiozero import OutputDevice


def _free_gpio_pin(pin):
    """
    Force-free a GPIO pin via lgpio so that gpiozero can claim it.
    Handles both RPi 5 (gpiochip4) and RPi 4 (gpiochip0).
    Silently ignores errors if the pin is already free or lgpio is absent.
    """
    try:
        import lgpio
    except ImportError:
        return  # lgpio not installed — nothing to free

    for chip in (4, 0):          # RPi 5 first, then RPi 4
        try:
            h = lgpio.gpiochip_open(chip)
            try:
                lgpio.gpio_free(h, pin)
                print(f"[Pump] Freed GPIO{pin} on gpiochip{chip}")
            except Exception:
                pass             # pin was not claimed — fine
            lgpio.gpiochip_close(h)
        except Exception:
            pass                 # chip doesn't exist on this board


class PumpController:
    """
    GPIO pump control using gpiozero — compatible with RPi 4 and RPi 5 / Bookworm.

    API:
      - set_threshold(), set_duration(), set_status_callback()
      - auto_check(weight), manual_on(), manual_off(), is_on, cleanup()
    """

    def __init__(self, pin=17, status_callback=None,
                 default_duration_min=2, initial_threshold_g=0,
                 active_high=True):
        self.pin = pin
        self._status_callback = status_callback
        self._lock = threading.Lock()
        self._is_on = False
        self._stop_event = threading.Event()
        self._timer_thread = None

        self.threshold_g = float(initial_threshold_g)
        self.duration_min = float(default_duration_min)

        # Free the pin first in case a previous run left it claimed ("GPIO busy")
        _free_gpio_pin(pin)

        # Setup GPIO via gpiozero (works on RPi 4 and RPi 5)
        self._device = OutputDevice(pin, active_high=active_high, initial_value=False)
        print(f"[Pump] GPIO pin {pin} initialised via gpiozero.")

    # ------------------ Properties ------------------
    @property
    def is_on(self) -> bool:
        with self._lock:
            return self._is_on

    # ------------------ Configuration ------------------
    def set_threshold(self, grams: float):
        self.threshold_g = float(grams)

    def set_duration(self, minutes: float):
        self.duration_min = float(minutes)

    def set_status_callback(self, fn):
        self._status_callback = fn

    # ------------------ Auto trigger ------------------
    def auto_check(self, weight_g: float):
        """Call whenever new weight arrives; starts timed run if > threshold and OFF."""
        try:
            w = float(weight_g)
        except (TypeError, ValueError):
            return
        if w > self.threshold_g and not self.is_on:
            self.start_timed(self.duration_min)

    # ------------------ Manual control ------------------
    def manual_on(self):
        self._turn_on()

    def manual_off(self):
        self._turn_off()

    # ------------------ Timed run ------------------
    def start_timed(self, duration_min: float):
        """Turn ON immediately, then OFF after duration in a background thread."""
        self._turn_on()
        with self._lock:
            if self._timer_thread and self._timer_thread.is_alive():
                self._stop_event.set()
            self._stop_event = threading.Event()
            t = threading.Thread(target=self._timed_worker,
                                 args=(float(duration_min), self._stop_event),
                                 daemon=True)
            self._timer_thread = t
            t.start()

    def _timed_worker(self, duration_min: float, stop_event: threading.Event):
        total_sec = max(0.0, 60.0 * duration_min)
        waited = 0.0
        step = 0.25
        while waited < total_sec and not stop_event.is_set():
            time.sleep(step)
            waited += step
        if not stop_event.is_set():
            self._turn_off()

    # ------------------ Low-level control ------------------
    def _turn_on(self):
        with self._lock:
            self._device.on()
            self._is_on = True
        if self._status_callback:
            try:
                self._status_callback("ON")
            except Exception:
                pass
        print("[Pump] ON")

    def _turn_off(self):
        with self._lock:
            self._device.off()
            self._is_on = False
            if self._timer_thread and self._timer_thread.is_alive():
                self._stop_event.set()
        if self._status_callback:
            try:
                self._status_callback("OFF")
            except Exception:
                pass
        print("[Pump] OFF")

    # ------------------ Cleanup ------------------
    def cleanup(self):
        try:
            self._turn_off()
        finally:
            self._device.close()
            print("[Pump] GPIO cleaned up")
