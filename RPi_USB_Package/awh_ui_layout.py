import tkinter as tk
from tkinter import ttk
from datetime import datetime
import threading
import requests


class AWHControlPanel(tk.Tk):
    """AWH Station Control Panel with Station Registry support."""

    def __init__(self, controller=None, backend_url="http://localhost:8000"):
        super().__init__()

        self.controller = controller
        self.backend_url = backend_url

        self.title("AWH Station Control Panel")
        self.geometry("900x780")
        self.minsize(800, 660)

        self._build_layout()

        # UI state flags
        self._is_validated = False
        self._is_running = False
        self._selected_station = None
        self._stations = []

        # Initialize button states, then fetch stations async
        self._update_button_states()
        threading.Thread(target=self._fetch_stations, daemon=True).start()

    def _fetch_stations(self):
        """Fetch available stations from backend registry (runs in background thread)."""
        try:
            response = requests.get(
                f"{self.backend_url}/stations-registry",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                self._stations = [s["station_name"] for s in data.get("stations", [])]
                self.after(0, self._populate_station_dropdown)
                msg = f"Loaded {len(self._stations)} stations from registry"
            else:
                msg = f"Registry returned status {response.status_code}"
        except Exception as e:
            msg = f"Could not reach backend: {e}"
        self.after(0, lambda m=msg: self.station_registry_label.config(text=f"Registry: {m}"))

    def _populate_station_dropdown(self):
        """Populate station selector combobox (called on main thread)."""
        if self._stations:
            self.station_selector.config(values=self._stations, state="readonly")
            self.station_selector.set(self._stations[0])
            self.connect_station_btn.config(state="normal")
        else:
            self.station_registry_label.config(text="Registry: No stations found")

    def _on_select_station(self, _event=None):
        """Enable Connect button when a station is selected from dropdown."""
        if self.station_selector.get():
            self.connect_station_btn.config(state="normal")

    def _on_connect_station(self):
        """Lock station selector and unlock configuration section."""
        selected = self.station_selector.get()
        if not selected:
            return

        self._selected_station = selected

        # Lock station selector
        self.station_selector.config(state="disabled")
        self.connect_station_btn.config(state="disabled")

        # Unlock configuration
        self.sensor_interval.config(state="readonly")
        self.file_interval.config(state="readonly")
        self.weight_threshold.config(state="readonly")
        self.pump_duration.config(state="readonly")

        self.config_status.config(text="Configuration Status: READY 🟡")
        self.system_status_label.config(
            text=f"STATION SELECTED 🟡  {selected}",
            foreground="orange"
        )
        self._update_button_states()

    def _update_button_states(self):
        """Enable/disable buttons based on UI state."""
        if self._is_running:
            # When running: only Stop is enabled
            self.validate_btn.config(state="disabled")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
        else:
            # When not running
            self.validate_btn.config(
                state="normal" if self._selected_station else "disabled"
            )
            self.stop_btn.config(state="disabled")
            self.start_btn.config(
                state="normal" if self._is_validated else "disabled"
            )

    def _on_validate(self):
        """Validate current configuration."""
        if not self._selected_station:
            self.config_status.config(text="Configuration Status: SELECT STATION FIRST 🔴")
            return

        if self.controller:
            interval_sec = int(self.sensor_interval.get().split()[0])
            file_interval_hr = int(self.file_interval.get().split()[0])
            threshold_g = float(self.weight_threshold.get().split()[0])
            pump_duration_min = float(self.pump_duration.get().split()[0])

            self.controller.set_interval(interval_sec)
            self.controller.set_file_saving_interval(file_interval_hr * 60)
            self.controller.set_threshold(threshold_g)
            self.controller.set_pump_duration(pump_duration_min)
            self.controller.set_station_name(self._selected_station)

        self._is_validated = True
        self.config_status.config(text="Configuration Status: VALIDATED 🟡")
        self._update_button_states()

    def _on_start(self):
        """Start acquisition."""
        if not self._is_validated or not self._selected_station:
            return

        if self.controller:
            self.controller.start_reading()

        self._is_running = True

        # Update UI state
        self.system_status_label.config(
            text=f"RUNNING 🟢  {self._selected_station}",
            foreground="green"
        )
        self.config_status.config(text="Configuration Status: LOCKED 🟢")
        self.lock_label.config(text="Config Lock: ON")
        self.csv_status_label.config(text="CSV Logging: Active")
        self.cloud_status_label.config(text="Cloud Upload: Active")

        # Disable all config inputs
        self.sensor_interval.config(state="disabled")
        self.file_interval.config(state="disabled")
        self.weight_threshold.config(state="disabled")
        self.pump_duration.config(state="disabled")

        self._update_button_states()

    def _on_stop(self):
        """Stop acquisition."""
        if self.controller:
            self.controller.stop_reading()

        self._is_running = False

        # Update UI state
        self.system_status_label.config(
            text=f"STOPPED 🔴  {self._selected_station}",
            foreground="red"
        )
        self.config_status.config(text="Configuration Status: VALIDATED 🟡")
        self.lock_label.config(text="Config Lock: OFF")
        self.csv_status_label.config(text="CSV Logging: Inactive")
        self.cloud_status_label.config(text="Cloud Upload: N/A")

        # Re-enable config inputs
        self.sensor_interval.config(state="readonly")
        self.file_interval.config(state="readonly")
        self.weight_threshold.config(state="readonly")
        self.pump_duration.config(state="readonly")

        self._update_button_states()

    def _build_layout(self):
        """Build scrollable layout infrastructure."""
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window(
            (0, 0),
            window=self.scrollable_frame,
            anchor="nw"
        )

        # Force embedded frame to always match canvas width
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(
                self.canvas_window, width=e.width
            )
        )

        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Centered content wrapper
        self.content = ttk.Frame(self.scrollable_frame)
        self.content.pack(fill="x", expand=True)
        self.content.columnconfigure(0, weight=1)

        self._build_header()
        self._build_station_selection()
        self._build_configuration()
        self._build_controls()
        self._build_status()

    def _build_header(self):
        """Build header section (visual only)."""
        frame = ttk.Frame(self.content, padding=10)
        frame.pack(fill="x", padx=40)

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)

        # LEFT: Station title + subtitle
        left_title = ttk.Frame(frame)
        left_title.grid(row=0, column=0, sticky="w")

        ttk.Label(
            left_title,
            text="AWH Station Control Panel",
            font=("Helvetica", 16, "bold"),
            justify="left"
        ).pack(anchor="w")

        ttk.Label(
            left_title,
            text="Multi-Station Deployment",
            font=("Helvetica", 12),
            justify="left"
        ).pack(anchor="w")

        # RIGHT: Status + time
        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="e")

        self.system_status_label = ttk.Label(
            right,
            text="SELECT STATION 🔵",
            font=("Helvetica", 14, "bold"),
            foreground="blue"
        )
        self.system_status_label.pack(anchor="e")

        self.time_label = ttk.Label(
            right,
            text=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        self.time_label.pack(anchor="e")

    def _build_station_selection(self):
        """Build station selection section (Step 1)."""
        frame = ttk.LabelFrame(
            self.content,
            text="Step 1: Select Station",
            padding=10
        )
        frame.pack(fill="x", padx=40, pady=10)
        frame.columnconfigure(1, weight=1)

        self.station_registry_label = ttk.Label(
            frame,
            text="Registry: Loading stations...",
            font=("Helvetica", 9),
            foreground="blue"
        )
        self.station_registry_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(frame, text="Station:").grid(row=1, column=0, sticky="w")
        self.station_selector = ttk.Combobox(
            frame,
            values=[],
            state="disabled",
            width=40
        )
        self.station_selector.grid(row=1, column=1, padx=10, sticky="w")
        self.station_selector.bind("<<ComboboxSelected>>", self._on_select_station)

        self.connect_station_btn = ttk.Button(
            frame,
            text="Connect to Station",
            command=self._on_connect_station,
            state="disabled"
        )
        self.connect_station_btn.grid(row=1, column=2, padx=10)

    def _build_configuration(self):
        """Build configuration section (Step 2)."""
        cfg = ttk.LabelFrame(
            self.content,
            text="Step 2: Configuration (Before Start)",
            padding=10
        )
        cfg.pack(fill="x", padx=40, pady=10)

        # Sampling Settings
        sampling = ttk.LabelFrame(cfg, text="Sampling Settings", padding=10)
        sampling.pack(fill="x", pady=5)
        sampling.columnconfigure(1, weight=1)

        ttk.Label(sampling, text="Sensor Interval").grid(row=0, column=0, sticky="w")
        self.sensor_interval = ttk.Combobox(
            sampling,
            values=["1 s", "2 s", "5 s", "10 s", "30 s"],
            state="disabled",
            width=12
        )
        self.sensor_interval.set("10 s")
        self.sensor_interval.grid(row=0, column=1, padx=10, sticky="w")

        ttk.Label(sampling, text="File Interval").grid(row=1, column=0, sticky="w")
        self.file_interval = ttk.Combobox(
            sampling,
            values=["1 hr", "2 hr", "4 hr", "6 hr"],
            state="disabled",
            width=12
        )
        self.file_interval.set("1 hr")
        self.file_interval.grid(row=1, column=1, padx=10, sticky="w")

        # Pump Automation
        pump = ttk.LabelFrame(cfg, text="Pump Automation", padding=10)
        pump.pack(fill="x", pady=5)
        pump.columnconfigure(1, weight=1)

        ttk.Label(pump, text="Weight Threshold").grid(row=0, column=0, sticky="w")
        self.weight_threshold = ttk.Combobox(
            pump,
            values=["1000 g", "1500 g", "2000 g", "3000 g"],
            state="disabled",
            width=14
        )
        self.weight_threshold.set("2000 g")
        self.weight_threshold.grid(row=0, column=1, padx=10, sticky="w")

        ttk.Label(pump, text="Pump Duration").grid(row=1, column=0, sticky="w")
        self.pump_duration = ttk.Combobox(
            pump,
            values=["1 min", "2 min", "3 min", "4 min", "5 min"],
            state="disabled",
            width=12
        )
        self.pump_duration.set("2 min")
        self.pump_duration.grid(row=1, column=1, padx=10, sticky="w")

        self.config_status = ttk.Label(
            cfg,
            text="Configuration Status: SELECT STATION FIRST 🔵"
        )
        self.config_status.pack(anchor="w", pady=(8, 0))

    def _build_controls(self):
        """Build controls section (Step 3)."""
        ctrl = ttk.LabelFrame(
            self.content,
            text="Step 3: System Control",
            padding=10
        )
        ctrl.pack(fill="x", padx=40, pady=10)

        self.validate_btn = ttk.Button(
            ctrl,
            text="Validate Configuration",
            command=self._on_validate
        )
        self.start_btn = ttk.Button(
            ctrl,
            text="Start Acquisition",
            state="disabled",
            command=self._on_start
        )
        self.stop_btn = ttk.Button(
            ctrl,
            text="Stop Acquisition",
            state="disabled",
            command=self._on_stop
        )

        self.validate_btn.pack(pady=5)
        self.start_btn.pack(pady=5)
        self.stop_btn.pack(pady=5)

        self.lock_label = ttk.Label(ctrl, text="Config Lock: OFF")
        self.lock_label.pack(pady=(8, 0))

    def _build_status(self):
        """Build status section (read-only)."""
        status = ttk.LabelFrame(
            self.content,
            text="System Status",
            padding=10
        )
        status.pack(fill="x", padx=40, pady=10)

        status.columnconfigure(0, weight=1)
        status.columnconfigure(1, weight=0)

        # LEFT: Status info
        left = ttk.Frame(status)
        left.grid(row=0, column=0, sticky="w")

        self.runtime_label = ttk.Label(left, text="Runtime: 00:00:00")
        self.runtime_label.pack(anchor="w")

        self.pump_status_label = ttk.Label(left, text="Pump Status: OFF")
        self.pump_status_label.pack(anchor="w")

        self.csv_status_label = ttk.Label(left, text="CSV Logging: Inactive")
        self.csv_status_label.pack(anchor="w")

        self.cloud_status_label = ttk.Label(left, text="Cloud Upload: N/A")
        self.cloud_status_label.pack(anchor="w")

        # RIGHT: Sensor health
        right = ttk.Frame(status)
        right.grid(row=0, column=1, sticky="e", padx=30)

        ttk.Label(right, text="Sensor Health", font=("Helvetica", 12, "bold")).pack(anchor="w")

        for sensor in [
            "Balance", "Power", "Flow", "Intake Air", "Outtake Air"
        ]:
            ttk.Label(right, text=f"{sensor}: ✔").pack(anchor="w")

    def update_status(self, data_str):
        """Receive backend CSV-format row and refresh status labels."""
        try:
            fields = data_str.split(",")
            if len(fields) >= 13:
                runtime = fields[12]
                self.runtime_label.config(text=f"Runtime: {runtime}")

                cloud_text = "Cloud Upload: Active"
                self.cloud_status_label.config(text=cloud_text)
        except Exception:
            pass

    def update_pump_status(self, status):
        """Receive pump status callback from backend."""
        try:
            is_on = bool(status)
            self.pump_status_label.config(text=f"Pump Status: {'ON' if is_on else 'OFF'}")
        except Exception:
            self.pump_status_label.config(text="Pump Status: OFF")


if __name__ == "__main__":
    app = AWHControlPanel()
    app.mainloop()
