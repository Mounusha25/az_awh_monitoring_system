# testunit.py
# ============================================
# Test Unit Controller (Main Process)
# - Uses read_balance.py for Balance
# - Uses read_power.py for Power Meter
# - Uses read_flow.py for Flow Meter
# - Reads Intake/Outtake Anemometers
# - Controls Pump via PumpController
# - Saves CSV, uploads to Cloud, optional email sending
# ============================================

# ---------- Standard Library ----------
import os
import threading
import time
import csv
from datetime import datetime, timedelta

# ---------- Third-Party ----------
import requests

# ---------- Local Modules ----------
from intake_anemometer import intake_anemometer
from outtake_anemometer import outtake_anemometer
from pump_controller import PumpController
from read_balance import BalanceSerialReader, parse_balance_line
from read_power import PowerMeterReader
from read_flow import FlowMeterReader
from awh_ui_layout import Application

# ============================================
# Configuration / Constants
# ============================================

STATION_NAME = "station_AquaPars #2 @Power Station, Tempe"
CLOUD_URL = "https://us-central1-awh-project-460421.cloudfunctions.net/receive_data"

# Upload throttle (seconds)
CLOUD_UPLOAD_EVERY_SEC = 60  # send to cloud at most once every 60s

# Reader watchdog
READER_STALE_SEC = 45        # if no new data for this many seconds, restart that reader
WATCHDOG_POLL_SEC = 5        # how often the watchdog checks staleness

# ============================================
# Utilities
# ============================================

def send_to_cloud(station_name, data):
    """POST one measurement record to the Cloud Function."""
    payload = {
        "station_name": station_name,
        "temperature": data.get("temperature"),
        "humidity": data.get("humidity"),
        "velocity": data.get("velocity"),
        "unit": data.get("unit"),
        "outtake_temperature": data.get("outtake_temperature"),
        "outtake_humidity": data.get("outtake_humidity"),
        "outtake_velocity": data.get("outtake_velocity"),
        "outtake_unit": data.get("outtake_unit"),
        "voltage": data.get("voltage"),
        "current": data.get("current"),
        "power": data.get("power"),
        "energy": data.get("energy"),
        "weight": data.get("weight"),
        "pump_status": data.get("pump_status"),
        "flow_lmin": data.get("flow_lmin"),
        "flow_hz": data.get("flow_hz"),
        "flow_total": data.get("flow_total"),
    }
    try:
        response = requests.post(CLOUD_URL, json=payload, timeout=10)
        print(f"[Cloud Upload] {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[Cloud Upload] Failed: {e}")

# ============================================
# Core Controller
# ============================================

class BalanceReader:
    """
    Main controller for reading sensors and coordinating outputs.
    - Balance from read_balance.BalanceSerialReader
    - Power meter from read_power.PowerMeterReader
    - Flow meter from read_flow.FlowMeterReader
    - Intake/Outtake anemometers
    - Pump control is delegated to PumpController
    - CSV saving & Cloud upload
    """

    def __init__(self, callback, csv_dir, update_pump_status_callback, pump: PumpController):
        self.callback = callback
        self.csv_dir = csv_dir
        self.csv_files = []
        self.sent_files = set()
        self.running = False

        # Timers & settings
        self.interval = 10
        self.file_saving_interval_minutes = 1
        self.start_time = None
        self.last_file_time = datetime.now()

        # Cloud upload throttle
        self.cloud_upload_interval_secs = CLOUD_UPLOAD_EVERY_SEC
        self._last_cloud_upload_ts = 0.0  # 0 => first call uploads immediately

        # Live data
        self.current_weight = None
        self.balance_line = None
        self.power_tuple = None
        self.flow_tuple = None
        self.intake_air_data = (None, None, None, "m/s")
        self.outtake_air_data = (None, None, None, "m/s")

        # Last-seen timestamps for watchdog
        now_ts = time.time()
        self._last_balance_ts = now_ts
        self._last_power_ts = now_ts
        self._last_flow_ts = now_ts

        # Pump
        self.pump = pump
        self.pump.set_status_callback(update_pump_status_callback)

        # CSV
        self.create_new_csv_file()

        # Readers (created but started in start_reading)
        self.balance_reader = None
        self.power_reader = None
        self.flow_reader = None

    # ---------- Reader (Re)Starters ----------
    def _start_balance_reader(self):
        try:
            self.balance_reader = BalanceSerialReader(callback=self._on_balance_line, interval=self.interval)
            self.balance_reader.start()
            self._last_balance_ts = time.time()
            print("[Watchdog] Balance reader started.")
        except Exception as e:
            print(f"[Watchdog] Failed to start balance reader: {e}")

    def _start_power_reader(self):
        try:
            self.power_reader = PowerMeterReader(callback=self._on_power_data, interval=self.interval)
            self.power_reader.start()
            self._last_power_ts = time.time()
            print("[Watchdog] Power reader started.")
        except Exception as e:
            print(f"[Watchdog] Failed to start power reader: {e}")

    def _start_flow_reader(self):
        try:
            self.flow_reader = FlowMeterReader(pin=27, interval=self.interval, callback=self._on_flow_data)
            self.flow_reader.start()
            self._last_flow_ts = time.time()
            print("[Watchdog] Flow reader started.")
        except Exception as e:
            print(f"[Watchdog] Failed to start flow reader: {e}")

    def _restart_reader(self, which: str):
        """Best-effort stop + fresh re-create for a given reader ('balance'|'power'|'flow')."""
        try:
            if which == "balance" and self.balance_reader:
                try: self.balance_reader.stop()
                except: pass
                self.balance_reader = None
                print("[Watchdog] Balance reader stopped.")
                self._start_balance_reader()

            elif which == "power" and self.power_reader:
                try: self.power_reader.stop()
                except: pass
                self.power_reader = None
                print("[Watchdog] Power reader stopped.")
                self._start_power_reader()

            elif which == "flow" and self.flow_reader:
                try: self.flow_reader.stop()
                except: pass
                self.flow_reader = None
                print("[Watchdog] Flow reader stopped.")
                self._start_flow_reader()
        except Exception as e:
            print(f"[Watchdog] Failed to restart {which} reader: {e}")

    # ---------- Callbacks from readers ----------
    def _on_balance_line(self, line: str):
        # Receiving any line means the connection is alive
        self._last_balance_ts = time.time()
        parsed = parse_balance_line(line)
        if parsed:
            ST, GS, check, weight, unit = parsed
            self.current_weight = weight
            self.balance_line = (ST, GS, check, weight, unit)
            # Keep your auto pump logic
            try:
                self.pump.auto_check(weight)
            except Exception as e:
                print(f"[Pump] auto_check error: {e}")

    def _on_power_data(self, V, A, W, Wh):
        self._last_power_ts = time.time()
        self.power_tuple = (V, A, W, Wh)

    def _on_flow_data(self, flow_lmin, hz, total_liters):
        self._last_flow_ts = time.time()
        self.flow_tuple = (flow_lmin, hz, total_liters)

    # ---------- CSV ----------
    def create_new_csv_file(self):
        os.makedirs(self.csv_dir, exist_ok=True)
        now = datetime.now()
        file_name = now.strftime('%Y%m%d_%H%M') + '_measure_data.csv'
        file_path = os.path.join(self.csv_dir, file_name)
        self.csv_file = open(file_path, 'a', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow([
            'Date', 'Time', 'ST', 'GS', 'Check', 'Weight', 'Unit', 'Pump-Status',
            'Voltage (V)', 'Current (A)', 'Power (W)', 'Energy (Wh)', 'Operation Time',
            'Flow (L/min)', 'Frequency (Hz)', 'Total Flow (L)',
            'Intake Air Temperature (°C)', 'Intake Air Velocity', 'Intake Air Humidity (%)',
            'Outtake Air Temperature (°C)', 'Outtake Air Velocity', 'Outtake Air Humidity (% )'
        ])
        self.csv_files.append(file_path)

    # ---------- Lifecycle ----------
    def start_reading(self):
        if self.running:
            return
        self.running = True
        self.start_time = time.time()

        # Start readers
        self._start_balance_reader()
        self._start_power_reader()
        self._start_flow_reader()

        # Worker threads
        threading.Thread(target=self.save_data_to_csv_interval, daemon=True).start()
        threading.Thread(target=self.read_intake_air_data, daemon=True).start()
        threading.Thread(target=self.read_outtake_air_data, daemon=True).start()
        threading.Thread(target=self._watchdog_loop, daemon=True).start()

    def stop_reading(self):
        self.running = False
        try: self.balance_reader.stop()
        except: pass
        try: self.power_reader.stop()
        except: pass
        try: self.flow_reader.stop()
        except: pass
        try: self.csv_file.close()
        except: pass
        try: self.pump.cleanup()
        except: pass

    # ---------- Settings ----------
    def set_interval(self, interval):
        try:
            self.interval = int(interval)
            return True
        except ValueError:
            return False

    def set_file_saving_interval(self, interval):
        try:
            self.file_saving_interval_minutes = int(interval)
            return True
        except ValueError:
            return False

    def set_threshold(self, threshold):
        try:
            self.pump.set_threshold(float(threshold))
            return True
        except ValueError:
            return False

    def set_pump_duration(self, duration):
        try:
            self.pump.set_duration(float(duration))
            return True
        except ValueError:
            return False

    # ---------- Save / Cloud ----------
    def _operation_time_hms(self):
        elapsed = int(time.time() - self.start_time)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        return f"{h:02}:{m:02}:{s:02}"

    def save_data(self):
        now = datetime.now()
        now_ts = time.time()
        date_str = now.strftime('%Y-%m-%d')
        time_str = now.strftime('%H:%M:%S')
        op_time = self._operation_time_hms()

        if self.balance_line and self.power_tuple and self.flow_tuple:
            ST, GS, check, weight, unit = self.balance_line
            V, A, W, Wh = self.power_tuple
            flow_lmin, hz, total_liters = self.flow_tuple
            t_in, v_in, h_in, v_unit_in = self.intake_air_data
            t_out, v_out, h_out, v_unit_out = self.outtake_air_data
            pump_status_bit = 1 if self.pump.is_on else 0

            # Always write CSV (every second)
            self.csv_writer.writerow([
                date_str, time_str, ST, GS, check, weight, unit, pump_status_bit,
                V, A, W, Wh, op_time,
                flow_lmin, hz, total_liters,
                t_in, f"{v_in} {v_unit_in}" if v_in is not None else None, h_in,
                t_out, f"{v_out} {v_unit_out}" if v_out is not None else None, h_out
            ])

            # Throttled cloud upload (every CLOUD_UPLOAD_EVERY_SEC)
            if (now_ts - self._last_cloud_upload_ts) >= self.cloud_upload_interval_secs:
                send_to_cloud(STATION_NAME, {
                    "temperature": t_in, "humidity": h_in, "velocity": v_in, "unit": v_unit_in,
                    "outtake_temperature": t_out, "outtake_humidity": h_out, "outtake_velocity": v_out, "outtake_unit": v_unit_out,
                    "voltage": V, "current": A, "power": W, "energy": Wh,
                    "weight": weight, "pump_status": pump_status_bit,
                    "flow_lmin": flow_lmin, "flow_hz": hz, "flow_total": total_liters
                })
                self._last_cloud_upload_ts = now_ts

            # Update UI every second
            if self.callback:
                self.callback(",".join(map(str, [
                    date_str, time_str, ST, GS, check, weight, unit, pump_status_bit,
                    V, A, W, Wh, op_time,
                    flow_lmin, hz, total_liters,
                    t_in, v_in, h_in, v_unit_in,
                    t_out, v_out, h_out, v_unit_out
                ])))

    def save_data_to_csv_interval(self):
        while self.running:
            current_time = datetime.now()
            if current_time - self.last_file_time >= timedelta(minutes=self.file_saving_interval_minutes):
                try: self.csv_file.close()
                except: pass
                self.create_new_csv_file()
                self.last_file_time = current_time
            self.save_data()
            time.sleep(1)

    # ---------- Watchdog ----------
    def _watchdog_loop(self):
        """Restarts a reader if it hasn't produced data for READER_STALE_SEC seconds."""
        while self.running:
            try:
                now_ts = time.time()

                # Balance stale?
                if (now_ts - self._last_balance_ts) > READER_STALE_SEC:
                    print("[Watchdog] Balance reader stale. Attempting restart...")
                    self._restart_reader("balance")
                    # After attempting restart, give the device a moment
                    time.sleep(2)

                # Power stale?
                if (now_ts - self._last_power_ts) > READER_STALE_SEC:
                    print("[Watchdog] Power reader stale. Attempting restart...")
                    self._restart_reader("power")
                    time.sleep(2)

                # Flow stale?
                if (now_ts - self._last_flow_ts) > READER_STALE_SEC:
                    print("[Watchdog] Flow reader stale. Attempting restart...")
                    self._restart_reader("flow")
                    time.sleep(2)

            except Exception as e:
                print(f"[Watchdog] Error: {e}")
            time.sleep(WATCHDOG_POLL_SEC)

    # ---------- Anemometers ----------
    def read_intake_air_data(self):
        while self.running:
            try:
                h, t, v, unit = intake_anemometer()
                self.intake_air_data = (t, max(0, v), h, unit)
            except Exception as e:
                # If USB sensor disappears, just log and try again next loop
                print(f"[Intake] Error: {e}")
            time.sleep(self.interval)

    def read_outtake_air_data(self):
        while self.running:
            try:
                h, t, v, unit = outtake_anemometer()
                self.outtake_air_data = (t, max(0, v), h, unit)
            except Exception as e:
                # If USB sensor disappears, just log and try again next loop
                print(f"[Outtake] Error: {e}")
            time.sleep(self.interval)

# ============================================
# Entry Point
# ============================================

def main():
    csv_dir = 'measure_data'
    os.makedirs(csv_dir, exist_ok=True)

    pump = PumpController(pin=17, status_callback=None, default_duration_min=2, initial_threshold_g=0)
    controller = BalanceReader(
        callback=None,
        csv_dir=csv_dir,
        update_pump_status_callback=None,
        pump=pump
    )

    app = Application(balance_reader=controller)
    controller.callback = app.update_data
    controller.pump.set_status_callback(app.update_pump_status)

    app.mainloop()
    try:
        controller.csv_file.close()
    except:
        pass


if __name__ == "__main__":
    main()