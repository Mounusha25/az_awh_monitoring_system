import time
import threading
from gpiozero import Button


def _free_gpio_pin(pin):
    """Free a GPIO pin via lgpio in case a previous run left it claimed."""
    try:
        import lgpio
    except ImportError:
        return
    for chip in (4, 0):
        try:
            h = lgpio.gpiochip_open(chip)
            try:
                lgpio.gpio_free(h, pin)
            except Exception:
                pass
            lgpio.gpiochip_close(h)
        except Exception:
            pass


class FlowMeterReader:
    """
    Reads a pulse-output flow meter using gpiozero Button.
    SMWF-0420A spec (gpiozero version):
        - K ≈ 2800 pulses/L
        - Flow (L/min) = pulses_per_sec / 46.7
        - Volume (L)   = total_pulses / 2800
    """
    def __init__(self, pin=27, interval=1, callback=None):
        self.pin = pin
        self.interval = interval
        self.callback = callback
        self._running = False
        self._thread = None
        self._pulse_count = 0

        # Free the pin first in case a previous run left it claimed
        _free_gpio_pin(pin)

        # Use pull_up=True for open-collector output
        self.sensor = Button(self.pin, pull_up=True)
        self.sensor.when_pressed = self._pulse_callback

    def _pulse_callback(self):
        self._pulse_count += 1

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the background polling thread and release the GPIO pin.

        Waits for the background thread to fully exit, then closes the
        gpiozero Button so its edge-detection callback is unregistered before
        a restart claims the same pin again — otherwise a restart relies
        entirely on _free_gpio_pin()'s force-free to avoid a "pin already in
        use" error, and the old object's callback can keep firing into a
        _pulse_count nobody is reading anymore.
        """
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval + 3)
        try:
            self.sensor.close()
        except Exception:
            pass

    def _run(self):
        last_count = 0
        while self._running:
            time.sleep(self.interval)
            current_count = self._pulse_count
            pulses = current_count - last_count
            last_count = current_count

            hz = pulses / self.interval
            flow_lmin = hz / 46.7
            total_liters = current_count / 2800.0

            if self.callback:
                self.callback(flow_lmin, hz, total_liters)
