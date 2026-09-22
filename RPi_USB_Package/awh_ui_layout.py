import tkinter as tk
from tkinter import ttk
from datetime import datetime


class AWHControlPanel(tk.Tk):
    """AWH Station Control Panel (Hardcoded Station Mode)."""

    def __init__(self, controller=None, backend_url=None):
        super().__init__()

        self.controller = controller

        self.title("AWH Station Control Panel")
        self.geometry("900x900")
        self.minsize(800, 660)

        self._build_layout()

        self._is_validated = False
        self._is_running = False
        self._update_button_states()

    def _update_button_states(self):
        """Enable/disable buttons based on UI state."""
        if self._is_running:
            # Only Stop is enabled while running
            self.validate_btn.config(state="disabled")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
        else:
            self.validate_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.start_btn.config(
                state="normal" if self._is_validated else "disabled"
            )

    def _on_validate(self):
        """Validate current configuration."""
        if self.controller:
            interval_sec = int(self.sensor_interval.get().split()[0])
            file_interval_hr = int(self.file_interval.get().split()[0])
            threshold_g = float(self.weight_threshold.get().split()[0])
            pump_duration_min = float(self.pump_duration.get().split()[0])

            self.controller.set_interval(interval_sec)
            self.controller.set_file_saving_interval(file_interval_hr * 60)
            self.controller.set_threshold(threshold_g)
            self.controller.set_pump_duration(pump_duration_min)

        self._is_validated = True
        self.config_status.config(text="Configuration Status: VALIDATED 🟡")
        self._update_button_states()

    def _on_start(self):
        """Start acquisition."""
        if not self._is_validated:
            return

        if self.controller:
            self.controller.start_reading()

        self._is_running = True

        self.system_status_label.config(
            text="RUNNING 🟢",
            foreground="green"
        )
        self.config_status.config(text="Configuration Status: LOCKED 🟢")
        self.lock_label.config(text="Config Lock: ON")
        self.csv_status_label.config(text="CSV Logging: Active")
        self.cloud_status_label.config(text="Cloud Upload: Active")

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

        self.system_status_label.config(
            text="STOPPED 🔴",
            foreground="red"
        )
        self.config_status.config(text="Configuration Status: VALIDATED 🟡")
        self.lock_label.config(text="Config Lock: OFF")
        self.csv_status_label.config(text="CSV Logging: Inactive")
        self.cloud_status_label.config(text="Cloud Upload: N/A")

        self.sensor_interval.config(state="readonly")
        self.file_interval.config(state="readonly")
        self.weight_threshold.config(state="readonly")
        self.pump_duration.config(state="readonly")

        for lbl in self.param_labels.values():
            caption = lbl.cget("text").split(":")[0]
            lbl.config(text=f"{caption}: —")
        for sensor, lbl in self.sensor_health_labels.items():
            lbl.config(text=f"{sensor}: —", foreground="")

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

        self._bind_mousewheel()

    def _bind_mousewheel(self):
        """Enable mouse-wheel / trackpad scrolling over the canvas (cross-platform)."""
        def _on_wheel(event):
            if event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")
            else:
                delta = -1 if event.delta > 0 else 1
                self.canvas.yview_scroll(delta, "units")

        def _bind(_event):
            self.canvas.bind_all("<MouseWheel>", _on_wheel)
            self.canvas.bind_all("<Button-4>", _on_wheel)
            self.canvas.bind_all("<Button-5>", _on_wheel)

        def _unbind(_event):
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")

        self.canvas.bind("<Enter>", _bind)
        self.canvas.bind("<Leave>", _unbind)

        # Centered content wrapper
        self.content = ttk.Frame(self.scrollable_frame)
        self.content.pack(fill="x", expand=True)
        self.content.columnconfigure(0, weight=1)

        self._build_header()
        self._build_configuration()
        self._build_controls()
        self._build_status()
        self._build_live_parameters()

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
            text="READY 🔵",
            font=("Helvetica", 14, "bold"),
            foreground="blue"
        )
        self.system_status_label.pack(anchor="e")

        self.time_label = ttk.Label(
            right,
            text=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        self.time_label.pack(anchor="e")

    def _build_configuration(self):
        """Build configuration section (Step 1)."""
        cfg = ttk.LabelFrame(
            self.content,
            text="Step 1: Configuration",
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
            state="readonly",
            width=12
        )
        self.sensor_interval.set("10 s")
        self.sensor_interval.grid(row=0, column=1, padx=10, sticky="w")

        ttk.Label(sampling, text="File Interval").grid(row=1, column=0, sticky="w")
        self.file_interval = ttk.Combobox(
            sampling,
            values=["1 hr", "2 hr", "4 hr", "6 hr"],
            state="readonly",
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
            values=["1000 g", "1500 g", "2000 g", "3000 g", "5000 g", "6000 g"],
            state="readonly",
            width=14
        )
        self.weight_threshold.set("2000 g")
        self.weight_threshold.grid(row=0, column=1, padx=10, sticky="w")

        ttk.Label(pump, text="Pump Duration").grid(row=1, column=0, sticky="w")
        self.pump_duration = ttk.Combobox(
            pump,
            values=["1 min", "2 min", "3 min", "4 min", "5 min", "6 min"],
            state="readonly",
            width=12
        )
        self.pump_duration.set("2 min")
        self.pump_duration.grid(row=1, column=1, padx=10, sticky="w")

        self.config_status = ttk.Label(
            cfg,
            text="Configuration Status: READY 🔵"
        )
        self.config_status.pack(anchor="w", pady=(8, 0))

    def _build_controls(self):
        """Build controls section (Step 2)."""
        ctrl = ttk.LabelFrame(
            self.content,
            text="Step 2: System Control",
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

        self.sensor_health_labels = {}
        for sensor in [
            "Balance", "Power", "Flow", "Intake Air", "Outtake Air"
        ]:
            lbl = ttk.Label(right, text=f"{sensor}: —")
            lbl.pack(anchor="w")
            self.sensor_health_labels[sensor] = lbl

    def _build_live_parameters(self):
        """Build live sensor parameter readout (Step 3, read-only)."""
        live = ttk.LabelFrame(
            self.content,
            text="Live Parameters",
            padding=10
        )
        live.pack(fill="x", padx=40, pady=10)

        left = ttk.Frame(live)
        left.grid(row=0, column=0, sticky="nw", padx=(0, 30))

        right = ttk.Frame(live)
        right.grid(row=0, column=1, sticky="nw")

        ttk.Label(left, text="Intake Air", font=("Helvetica", 11, "bold")).pack(anchor="w")
        ttk.Label(right, text="Outtake Air", font=("Helvetica", 11, "bold")).pack(anchor="w")

        self.param_labels = {}
        param_defs = [
            (left, "intake_temp", "Temperature"),
            (left, "intake_humidity", "Humidity"),
            (left, "intake_velocity", "Velocity"),
            (right, "outtake_temp", "Temperature"),
            (right, "outtake_humidity", "Humidity"),
            (right, "outtake_velocity", "Velocity"),
        ]
        for parent, key, caption in param_defs:
            lbl = ttk.Label(parent, text=f"{caption}: —")
            lbl.pack(anchor="w")
            self.param_labels[key] = lbl

        bottom = ttk.Frame(live)
        bottom.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Label(bottom, text="Power / Weight / Flow", font=("Helvetica", 11, "bold")).pack(anchor="w")
        for key, caption in [
            ("weight", "Balance Weight"),
            ("voltage", "Voltage"),
            ("power", "Power"),
            ("energy", "Energy"),
            ("flow_lmin", "Flow Rate"),
        ]:
            lbl = ttk.Label(bottom, text=f"{caption}: —")
            lbl.pack(anchor="w")
            self.param_labels[key] = lbl

    @staticmethod
    def _fmt(raw, unit=""):
        """Format a raw string field, treating None/empty as missing."""
        if raw is None or raw in ("", "None", "nan"):
            return "—"
        return f"{raw}{unit}"

    def update_status(self, data_str):
        """Receive backend CSV-format row and refresh status labels.

        Field layout matches the callback built in AquaPars1.py / AquaPars1_new_pm.py:
        0 date, 1 time, 2 ST, 3 GS, 4 check, 5 weight, 6 unit, 7 pump_status,
        8 V, 9 A, 10 W, 11 Wh, 12 op_time,
        13 flow_lmin, 14 hz, 15 total_liters,
        16 t_in, 17 v_in, 18 h_in, 19 v_unit_in,
        20 t_out, 21 v_out, 22 h_out, 23 v_unit_out
        """
        try:
            fields = data_str.split(",")
            if len(fields) < 13:
                return

            runtime = fields[12]
            self.runtime_label.config(text=f"Runtime: {runtime}")
            self.cloud_status_label.config(text="Cloud Upload: Active")

            if len(fields) < 24:
                return

            weight, unit = fields[5], fields[6]
            V, A, W, Wh = fields[8], fields[9], fields[10], fields[11]
            flow_lmin = fields[13]
            t_in, v_in, h_in, v_unit_in = fields[16], fields[17], fields[18], fields[19]
            t_out, v_out, h_out, v_unit_out = fields[20], fields[21], fields[22], fields[23]

            self.param_labels["intake_temp"].config(text=f"Temperature: {self._fmt(t_in, ' C')}")
            self.param_labels["intake_humidity"].config(text=f"Humidity: {self._fmt(h_in, ' %')}")
            self.param_labels["intake_velocity"].config(
                text=f"Velocity: {self._fmt(v_in, f' {v_unit_in}' if v_in not in (None, 'None') else '')}"
            )

            self.param_labels["outtake_temp"].config(text=f"Temperature: {self._fmt(t_out, ' C')}")
            self.param_labels["outtake_humidity"].config(text=f"Humidity: {self._fmt(h_out, ' %')}")
            self.param_labels["outtake_velocity"].config(
                text=f"Velocity: {self._fmt(v_out, f' {v_unit_out}' if v_out not in (None, 'None') else '')}"
            )

            self.param_labels["weight"].config(
                text=f"Balance Weight: {self._fmt(weight, f' {unit}' if weight not in (None, 'None') else '')}"
            )
            self.param_labels["voltage"].config(text=f"Voltage: {self._fmt(V, ' V')}")
            self.param_labels["power"].config(text=f"Power: {self._fmt(W, ' W')}")
            self.param_labels["energy"].config(text=f"Energy: {self._fmt(Wh, ' kWh')}")
            self.param_labels["flow_lmin"].config(text=f"Flow Rate: {self._fmt(flow_lmin, ' L/min')}")

            self._update_sensor_health(
                balance_ok=weight not in (None, "", "None", "nan"),
                power_ok=any(v not in (None, "", "None", "nan") for v in (V, A, W, Wh)),
                flow_ok=flow_lmin not in (None, "", "None", "nan"),
                intake_ok=any(v not in (None, "", "None", "nan") for v in (t_in, h_in, v_in)),
                outtake_ok=any(v not in (None, "", "None", "nan") for v in (t_out, h_out, v_out)),
            )
        except Exception:
            pass

    def _update_sensor_health(self, balance_ok, power_ok, flow_ok, intake_ok, outtake_ok):
        """Refresh the Sensor Health checkmarks based on whether each sensor's
        latest fields are actually present (not None/stale placeholders)."""
        status = {
            "Balance": balance_ok,
            "Power": power_ok,
            "Flow": flow_ok,
            "Intake Air": intake_ok,
            "Outtake Air": outtake_ok,
        }
        for sensor, ok in status.items():
            lbl = self.sensor_health_labels.get(sensor)
            if lbl is None:
                continue
            if ok:
                lbl.config(text=f"{sensor}: ✔", foreground="green")
            else:
                lbl.config(text=f"{sensor}: ✖", foreground="red")

    def update_pump_status(self, status):
        """Receive pump status callback from backend."""
        try:
            is_on = str(status).strip().upper() in ("1", "ON", "TRUE")
            self.pump_status_label.config(text=f"Pump Status: {'ON' if is_on else 'OFF'}")
        except Exception:
            self.pump_status_label.config(text="Pump Status: OFF")


if __name__ == "__main__":
    app = AWHControlPanel()
    app.mainloop()
