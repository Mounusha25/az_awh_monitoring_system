# ui_display.py
# Tkinter UI for controlling and monitoring the Test Unit

import tkinter as tk
from tkinter import messagebox
from multiprocessing import Queue
import threading
from datetime import datetime
from send_mail import send_email_with_attachments, schedule_email


def add_hover_effect(widget, hover_color="lightblue"):
    """Utility: adds hover effect to a widget (cross-platform safe)."""
    normal_color = widget.cget("background")  # use the widget's default background

    def on_enter(event):
        widget.config(bg=hover_color)
    def on_leave(event):
        widget.config(bg=normal_color)

    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)


def _fmt(val, digits: int = 3):
    """Format numeric-looking values to a fixed number of decimals; otherwise return as-is."""
    try:
        f = float(val)
        return f"{f:.{digits}f}"
    except (TypeError, ValueError):
        return str(val) if val is not None else "--"


class Application(tk.Tk):
    """Tkinter UI for controlling and monitoring the station."""

    def __init__(self, balance_reader):
        super().__init__()
        self.balance_reader = balance_reader
        self.title("Test Unit @ HighBay Station")

        # Email queue (kept for compatibility)
        self.email_queue = Queue()

        # Build UI
        self.create_widgets()

        # Start background thread AFTER methods exist
        self.email_process = threading.Thread(target=self.process_email_queue, daemon=True)
        self.email_process.start()

        # Safely start clock after init
        self.update_time()

    # ---------- Widgets / Layout ----------
    def create_widgets(self):
        self.time_label = tk.Label(self, text="", font=("Helvetica", 14))
        self.time_label.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")

        self.start_button = tk.Button(self, text="Start", command=self.start_reading)
        self.start_button.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.start_button, "lightgreen")

        self.stop_button = tk.Button(self, text="Stop", command=self.stop_reading)
        self.stop_button.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.stop_button, "tomato")

        self.exit_button = tk.Button(self, text="Exit", command=self.exit_program)
        self.exit_button.grid(row=1, column=2, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.exit_button, "gray")

        # Interval controls
        self.interval_label = tk.Label(self, text="Interval Time (seconds):")
        self.interval_label.grid(row=2, column=0, padx=5, pady=5, sticky="e")
        self.interval_entry = tk.Entry(self)
        self.interval_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        self.interval_light = tk.Label(self, text="🔴", fg="red")
        self.interval_light.grid(row=2, column=3, padx=5, pady=5, sticky="w")
        self.set_interval_button = tk.Button(self, text="Set Interval", command=self.set_interval)
        self.set_interval_button.grid(row=2, column=2, padx=1, pady=1, sticky="ew")
        add_hover_effect(self.set_interval_button)

        # File interval controls
        self.file_interval_label = tk.Label(self, text="File Interval (minutes):")
        self.file_interval_label.grid(row=3, column=0, padx=5, pady=5, sticky="e")
        self.file_interval_entry = tk.Entry(self)
        self.file_interval_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")
        self.file_interval_light = tk.Label(self, text="🔴", fg="red")
        self.file_interval_light.grid(row=3, column=3, padx=5, pady=5, sticky="w")
        self.set_file_interval_button = tk.Button(self, text="Set File Interval", command=self.set_file_interval)
        self.set_file_interval_button.grid(row=3, column=2, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.set_file_interval_button)

        # Threshold controls
        self.threshold_label = tk.Label(self, text="Weight Threshold (g):")
        self.threshold_label.grid(row=4, column=0, padx=5, pady=5, sticky="e")
        self.threshold_entry = tk.Entry(self)
        self.threshold_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")
        self.threshold_light = tk.Label(self, text="🔴", fg="red")
        self.threshold_light.grid(row=4, column=3, padx=5, pady=5, sticky="w")
        self.set_threshold_button = tk.Button(self, text="Set Threshold", command=self.set_threshold)
        self.set_threshold_button.grid(row=4, column=2, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.set_threshold_button)

        # Pump duration controls
        self.pump_duration_label = tk.Label(self, text="Pump Duration (minutes):")
        self.pump_duration_label.grid(row=5, column=0, padx=5, pady=5, sticky="e")
        self.pump_duration_entry = tk.Entry(self)
        self.pump_duration_entry.grid(row=5, column=1, padx=5, pady=5, sticky="ew")
        self.pump_duration_light = tk.Label(self, text="🔴", fg="red")
        self.pump_duration_light.grid(row=5, column=3, padx=5, pady=5, sticky="w")
        self.set_pump_duration_button = tk.Button(self, text="Set Pump Duration", command=self.set_pump_duration)
        self.set_pump_duration_button.grid(row=5, column=2, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.set_pump_duration_button)

        # Email controls
        self.email_label = tk.Label(self, text="Recipient Emails (comma separated):")
        self.email_label.grid(row=6, column=0, padx=5, pady=5, sticky="e")
        self.email_entry = tk.Entry(self)
        self.email_entry.grid(row=6, column=1, padx=5, pady=5, sticky="ew")

        self.schedule_email_button = tk.Button(self, text="Schedule Email", command=self.schedule_email)
        self.schedule_email_button.grid(row=6, column=2, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.schedule_email_button)

        self.send_email_button = tk.Button(self, text="Send Email Manually", command=self.send_email_manually)
        self.send_email_button.grid(row=6, column=3, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.send_email_button)

        # Schedule time + manual pump
        self.schedule_time_label = tk.Label(self, text="Schedule Time (HH:MM):")
        self.schedule_time_label.grid(row=7, column=0, padx=5, pady=5, sticky="e")
        self.schedule_time_entry = tk.Entry(self)
        self.schedule_time_entry.grid(row=7, column=1, padx=5, pady=5, sticky="ew")

        self.manual_on_button = tk.Button(self, text="Manual Pump ON", command=self.manual_pump_on)
        self.manual_on_button.grid(row=7, column=2, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.manual_on_button, "lightgreen")

        self.manual_off_button = tk.Button(self, text="Manual Pump OFF", command=self.manual_pump_off)
        self.manual_off_button.grid(row=7, column=3, padx=5, pady=5, sticky="ew")
        add_hover_effect(self.manual_off_button, "tomato")

        # Status and readings
        self.status_label = tk.Label(self, text="Status: Stopped", fg="red")
        self.status_label.grid(row=9, column=0, columnspan=4, padx=5, pady=5)

        self.operation_time_label = tk.Label(self, text="Operation Time: --:--:--", font=("Helvetica", 12))
        self.operation_time_label.grid(row=10, column=0, columnspan=5, padx=5, pady=5, sticky="ew")

        self.weight_pump_status_label = tk.Label(self, text="Weight: -- g, Pump Status: OFF", font=("Helvetica", 12))
        self.weight_pump_status_label.grid(row=11, column=0, columnspan=5, padx=5, pady=5, sticky="ew")

        self.voltage_current_label = tk.Label(self, text="Voltage: -- V, Current: -- A, Power: -- W, Energy: -- Wh", font=("Helvetica", 12))
        self.voltage_current_label.grid(row=12, column=0, columnspan=5, padx=5, pady=5, sticky="ew")

        # Flow meter readings
        self.flow_label = tk.Label(self, text="Flow: -- L/min, Freq: -- Hz, Total: -- L", font=("Helvetica", 12))
        self.flow_label.grid(row=13, column=0, columnspan=5, padx=5, pady=5, sticky="ew")

        self.intake_air_label = tk.Label(self, text="Intake Air Temp: -- °C, Intake Air Velocity: -- m/s, Intake Air Humidity: -- %", font=("Helvetica", 12))
        self.intake_air_label.grid(row=14, column=0, columnspan=5, padx=5, pady=5, sticky="ew")

        self.outtake_air_label = tk.Label(self, text="Outtake Air Temp: -- °C, Outtake Air Velocity: -- m/s, Outtake Air Humidity: -- %", font=("Helvetica", 12))
        self.outtake_air_label.grid(row=15, column=0, columnspan=5, padx=5, pady=5, sticky="ew")

        # Expand columns evenly
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=1)
        self.grid_columnconfigure(3, weight=1)

    # ---------- UI Actions ----------
    def start_reading(self):
        self.reset_button_colors()
        self.balance_reader.start_reading()
        self.status_label.config(text="Status: Running", fg="green")

    def stop_reading(self):
        self.balance_reader.stop_reading()
        self.reset_button_colors()
        self.status_label.config(text="Status: Stopped", fg="red")

    def exit_program(self):
        if self.balance_reader.running:
            self.balance_reader.stop_reading()
        self.email_queue.put(None)
        self.destroy()

    def update_data(self, data):
        parsed = data.split(',')
        # Expecting 24 fields (with flow data included)
        if len(parsed) >= 24:
            (date_str, time_str, _ST, _GS, _check,
             weight, unit, pump_status,
             voltage, current, power, energy, op_time,
             flow_lmin, flow_hz, flow_total,
             t_in, v_in, h_in, v_unit_in,
             t_out, v_out, h_out, v_unit_out) = parsed

            self.operation_time_label.config(text=f"Operation Time: {op_time}")

            # Weight + pump
            self.weight_pump_status_label.config(
                text=f"Weight: {_fmt(weight)} {unit}, Pump Status: {'ON' if pump_status == '1' else 'OFF'}"
            )

            # Power
            self.voltage_current_label.config(
                text=f"Voltage: {_fmt(voltage)} V, Current: {_fmt(current)} A, "
                     f"Power: {_fmt(power)} W, Energy: {_fmt(energy)} Wh"
            )

            # Flow
            self.flow_label.config(
                text=f"Flow: {_fmt(flow_lmin)} L/min, Freq: {_fmt(flow_hz)} Hz, Total: {_fmt(flow_total)} L"
            )

            # Air (intake/outtake)
            v_in_disp = f"{_fmt(v_in)} {v_unit_in}" if v_in not in (None, "", "None") else "--"
            v_out_disp = f"{_fmt(v_out)} {v_unit_out}" if v_out not in (None, "", "None") else "--"

            self.intake_air_label.config(
                text=f"Intake Air Temp: {_fmt(t_in)} °C, Intake Air Velocity: {v_in_disp}, "
                     f"Intake Air Humidity: {_fmt(h_in)} %"
            )
            self.outtake_air_label.config(
                text=f"Outtake Air Temp: {_fmt(t_out)} °C, Outtake Air Velocity: {v_out_disp}, "
                     f"Outtake Air Humidity: {_fmt(h_out)} %"
            )

    def set_interval(self):
        if self.balance_reader.set_interval(self.interval_entry.get()):
            self.interval_light.config(text="🟢", fg="green")

    def set_file_interval(self):
        if self.balance_reader.set_file_saving_interval(self.file_interval_entry.get()):
            self.file_interval_light.config(text="🟢", fg="green")

    def set_threshold(self):
        if self.balance_reader.set_threshold(self.threshold_entry.get()):
            self.threshold_light.config(text="🟢", fg="green")

    def set_pump_duration(self):
        if self.balance_reader.set_pump_duration(self.pump_duration_entry.get()):
            self.pump_duration_light.config(text="🟢", fg="green")

    def manual_pump_on(self):
        self.balance_reader.pump.manual_on()

    def manual_pump_off(self):
        self.balance_reader.pump.manual_off()

    def update_pump_status(self, status):
        # Use latest weight for quick glance
        self.weight_pump_status_label.config(
            text=f"Weight: {_fmt(self.balance_reader.current_weight)} g, Pump Status: {status}"
        )

    def update_time(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_label.config(text=f"Current Time: {current_time}")
        self.after(1000, self.update_time)

    def reset_button_colors(self):
        self.interval_light.config(text="🔴", fg="red")
        self.file_interval_light.config(text="🔴", fg="red")
        self.threshold_light.config(text="🔴", fg="red")
        self.pump_duration_light.config(text="🔴", fg="red")

    # ---------- Email ----------
    def send_email_manually(self):
        recipients = self.email_entry.get()
        if recipients:
            recipient_list = [email.strip() for email in recipients.split(",")]
            result = send_email_with_attachments(recipient_list,
                                                 self.balance_reader.csv_files,
                                                 self.balance_reader.sent_files)
            if not result:
                messagebox.showinfo("Info", "No new files to send.")
        else:
            messagebox.showerror("Error", "Please enter recipient email addresses.")

    def schedule_email(self):
        recipients = self.email_entry.get()
        time_str = self.schedule_time_entry.get()
        if recipients and time_str:
            recipient_list = [email.strip() for email in recipients.split(",")]
            schedule_email(recipient_list,
                           self.balance_reader.csv_files,
                           time_str,
                           self.balance_reader.sent_files)
            messagebox.showinfo("Scheduled", f"Email scheduled at {time_str} daily")
        else:
            messagebox.showerror("Error", "Please enter recipient email addresses and time.")

    def process_email_queue(self):
        # Dummy loop for backward compatibility
        while True:
            task = self.email_queue.get()
            if task is None:
                break
