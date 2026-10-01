"""Manual visual check for the Live Parameters panel — not part of the station runtime.

Run: python3 _demo_ui.py
Then click Validate Configuration -> Start Acquisition and watch the
Live Parameters + Sensor Health sections update every second with
simulated sensor data. Click Stop to see it reset to em dashes.
"""
import random
from awh_ui_layout import AWHControlPanel


class FakeController:
    def __init__(self, app):
        self.app = app
        self._job = None

    def set_interval(self, *a, **k): pass
    def set_file_saving_interval(self, *a, **k): pass
    def set_threshold(self, *a, **k): pass
    def set_pump_duration(self, *a, **k): pass

    def start_reading(self):
        self._tick()

    def stop_reading(self):
        if self._job is not None:
            self.app.after_cancel(self._job)
            self._job = None

    def _tick(self):
        row = [
            "2026-09-22", "10:00:00", "ST", "GS", "ok",
            round(1200 + random.uniform(-5, 5), 1), "g", random.choice([0, 1]),
            round(12 + random.uniform(-0.3, 0.3), 2),
            round(0.5 + random.uniform(-0.05, 0.05), 3),
            round(6 + random.uniform(-0.5, 0.5), 2),
            round(0.002 + random.uniform(0, 0.001), 4),
            "00:01:23",
            round(1.2 + random.uniform(-0.1, 0.1), 2), 56, 3.4,
            round(25 + random.uniform(-1, 1), 1), round(0.8, 2), 40, "m/s",
            round(26 + random.uniform(-1, 1), 1), round(0.9, 2), 38, "m/s",
        ]
        self.app.update_status(",".join(map(str, row)))
        self.app.update_pump_status(row[7])
        self._job = self.app.after(1000, self._tick)


if __name__ == "__main__":
    app = AWHControlPanel()
    app.controller = FakeController(app)
    app.mainloop()
