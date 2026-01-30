import tkinter as tk
from tkinter import ttk
from datetime import datetime


class AWHControlPanel(tk.Tk):
    """Minimal, safe AWH Station Control Panel."""

    def __init__(self, controller=None):
        super().__init__()

        self.controller = controller  # backend comes later

        self.title("AWH Station Control Panel")
        self.geometry("900x700")
        self.minsize(800, 600)

        self._build_layout()

        # UI state flags
        self._is_validated = False
        self._is_running = False

        # Initialize button states
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
            self.validate_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.start_btn.config(
                state="normal" if self._is_validated else "disabled"
            )

    def _on_validate(self):
        """Validate current configuration."""
        self._is_validated = True
        self.config_status.config(
            text="Configuration Status: VALIDATED 🟡"
        )
        self._update_button_states()

    def _on_start(self):
        """Start acquisition (UI state only)."""
        if not self._is_validated:
            return  # safety guard

        self._is_running = True

        # Update UI state
        self.system_status_label.config(text="Status: RUNNING 🟢")
        self.config_status.config(text="Configuration Status: LOCKED 🟢")
        self.lock_label.config(text="Config Lock: ON")

        # Disable all config inputs
        self.sensor_interval.config(state="disabled")
        self.file_interval.config(state="disabled")
        self.weight_threshold.config(state="disabled")
        self.pump_duration.config(state="disabled")

        self._update_button_states()

    def _on_stop(self):
        """Stop acquisition (UI state only)."""
        self._is_running = False

        # Update UI state
        self.system_status_label.config(text="Status: STOPPED 🔴")
        self.config_status.config(text="Configuration Status: VALIDATED 🟡")
        self.lock_label.config(text="Config Lock: OFF")

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

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._build_header()
        self._build_configuration()
        self._build_controls()
        self._build_status()

    def _build_header(self):
        """Build header section (visual only)."""
        frame = ttk.Frame(self.scrollable_frame, padding=10)
        frame.pack(fill="x")

        ttk.Label(
            frame,
            text="AWH Station Control Panel",
            font=("Helvetica", 16, "bold")
        ).pack(anchor="w")

        self.system_status_label = ttk.Label(
            frame,
            text="Status: STOPPED 🔴",
            font=("Helvetica", 11)
        )
        self.system_status_label.pack(anchor="w")

        self.time_label = ttk.Label(frame, text="")
        self.time_label.pack(anchor="w")

    def _build_configuration(self):
        """Build configuration section (layout only)."""
        cfg = ttk.LabelFrame(
            self.scrollable_frame,
            text="Configuration (Before Start)",
            padding=10
        )
        cfg.pack(fill="x", padx=10, pady=10)

        # Sampling Settings
        sampling = ttk.LabelFrame(cfg, text="Sampling Settings", padding=10)
        sampling.pack(fill="x", pady=5)

        ttk.Label(sampling, text="Sensor Interval").grid(row=0, column=0, sticky="w")
        self.sensor_interval = ttk.Combobox(
            sampling,
            values=["1 s", "2 s", "5 s", "10 s", "30 s"],
            state="readonly"
        )
        self.sensor_interval.set("10 s")
        self.sensor_interval.grid(row=0, column=1, padx=10)

        ttk.Label(sampling, text="File Interval").grid(row=1, column=0, sticky="w")
        self.file_interval = ttk.Combobox(
            sampling,
            values=["1 hr", "2 hr", "4 hr", "6 hr"],
            state="readonly"
        )
        self.file_interval.set("1 hr")
        self.file_interval.grid(row=1, column=1, padx=10)

        # Pump Automation
        pump = ttk.LabelFrame(cfg, text="Pump Automation", padding=10)
        pump.pack(fill="x", pady=5)

        ttk.Label(pump, text="Weight Threshold").grid(row=0, column=0, sticky="w")
        self.weight_threshold = ttk.Combobox(
            pump,
            values=["1000 g", "1500 g", "2000 g", "3000 g"],
            state="readonly"
        )
        self.weight_threshold.set("2000 g")
        self.weight_threshold.grid(row=0, column=1, padx=10)

        ttk.Label(pump, text="Pump Duration").grid(row=1, column=0, sticky="w")
        self.pump_duration = ttk.Combobox(
            pump,
            values=["1 min", "2 min", "3 min", "4 min", "5 min"],
            state="readonly"
        )
        self.pump_duration.set("2 min")
        self.pump_duration.grid(row=1, column=1, padx=10)

        self.config_status = ttk.Label(
            cfg,
            text="Configuration Status: NOT APPLIED 🔴"
        )
        self.config_status.pack(anchor="w", pady=(8, 0))

    def _build_controls(self):
        """Build controls section (buttons only)."""
        ctrl = ttk.LabelFrame(
            self.scrollable_frame,
            text="System Control",
            padding=10
        )
        ctrl.pack(fill="x", padx=10, pady=10)

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
            self.scrollable_frame,
            text="System Status",
            padding=10
        )
        status.pack(fill="x", padx=10, pady=10)

        self.runtime_label = ttk.Label(status, text="Runtime: 00:00:00")
        self.runtime_label.pack(anchor="w")

        self.pump_status_label = ttk.Label(status, text="Pump Status: OFF")
        self.pump_status_label.pack(anchor="w")

        self.csv_status_label = ttk.Label(status, text="CSV Logging: Inactive")
        self.csv_status_label.pack(anchor="w")

        self.cloud_status_label = ttk.Label(status, text="Cloud Upload: N/A")
        self.cloud_status_label.pack(anchor="w")


if __name__ == "__main__":
    app = AWHControlPanel()
    app.mainloop()
