# AWH UI Quick Reference Card

## 🚀 Run the UI

```bash
# On Raspberry Pi:
cd RPi_USB_Package && python3 AquaPars1.py

# On Mac (simulation):
cd RPi_USB_Package && python3 sim_run_on_mac.py

# Standalone UI preview:
cd RPi_USB_Package && python3 awh_ui_layout.py
```

The window will open showing:
- Station name & location
- Current status (STOPPED 🔴)
- Configuration options
- Control buttons
- Live status display

---

## 📊 State Machine

```
        [VALIDATE]
            ↓
STOPPED → VALIDATED → RUNNING → STOPPED
  🔴         🟡          🟢        🔴
[START]              [STOP]
```

---

## 🎮 What Each Button Does

| Button | What It Does | When Available |
|--------|-------------|-----------------|
| **VALIDATE** | Confirms configuration | When STOPPED or RUNNING |
| **START** | Begins data acquisition | Only when VALIDATED |
| **STOP** | Ends data acquisition | Only when RUNNING |

---

## 🔒 Configuration Lock

**When STOPPED 🔴:**
- Config fields are ✅ **EDITABLE**
- Can change any setting

**When RUNNING 🟢:**
- Config fields are ❌ **LOCKED**
- Cannot change settings mid-run
- Protects data integrity

**Why?** Prevents accidental configuration changes while system is actively collecting data.

---

## 📋 Configuration Fields

| Field | Default | Purpose |
|-------|---------|---------|
| **Sensor Interval** | 30s | How often to read sensors |
| **File Interval** | 60s | How often to save to CSV |
| **Weight Threshold** | 50g | When to trigger alerts |
| **Pump Duration** | 10s | How long pump runs per cycle |

---

## 📊 Status Metrics

### Left Column (Acquisition)
- **Runtime**: Total time system has been running
- **Cycles**: Number of complete measurement cycles
- **Water**: Total water collected (liters)

### Right Column (Sensors)
- **Temperature**: Current air temperature (°C)
- **Humidity**: Current humidity level (%)
- **Velocity**: Air flow velocity (m/s)
- **Flow Rate**: Water flow rate (L/s)

---

## 💻 For Developers

### Import & Use

```python
from awh_ui_layout import AWHControlPanel

# Standalone mode
app = AWHControlPanel()
app.mainloop()

# With controller
class MyController:
    def __init__(self):
        self.ui = AWHControlPanel(controller=self)

controller = MyController()
controller.ui.mainloop()
```

### Access UI State

```python
app = AWHControlPanel()

# Check current state
if app._is_validated:
    print("Configuration validated")
    
if app._is_running:
    print("System running")

# Get current config values
sensor_interval = app.sensor_interval.get()
file_interval = app.file_interval.get()
weight_threshold = app.weight_threshold.get()
pump_duration = app.pump_duration.get()
```

### Connect Backend

```python
class AWHBackend:
    def __init__(self):
        self.ui = AWHControlPanel(controller=self)
        
        # Connect button callbacks
        self.ui.validate_btn.config(command=self.on_validate)
        self.ui.start_btn.config(command=self.on_start)
        self.ui.stop_btn.config(command=self.on_stop)
        
    def on_validate(self):
        """User clicked VALIDATE"""
        print("Preparing system...")
        
    def on_start(self):
        """User clicked START"""
        print("Starting acquisition...")
        # Start polling sensors
        # Upload to Firebase
        
    def on_stop(self):
        """User clicked STOP"""
        print("Stopping acquisition...")
        # Stop polling
        # Save to CSV
        # Close connections
```

---

## 🎨 Customization Examples

### Change Station Name

Edit line ~100 in `awh_ui_layout.py`:

```python
text="Station: YOUR_STATION_NAME @ YOUR_LOCATION"
```

### Change Default Configuration Values

Edit the dropdowns creation (search for `OptionMenu` or `Combobox`):

```python
sensor_options = ["15s", "30s", "60s", "120s"]  # Add/remove as needed
```

### Add New Status Metric

Find `_build_status()` method and add:

```python
new_metric = ttk.Label(
    metrics_left,
    text="New Metric: Value",
    font=("Helvetica", 11)
)
new_metric.pack(anchor="w", pady=5)
```

---

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| Window won't open | Use `python3.14` instead of `python3` |
| Buttons don't respond | Check Python version (needs 3.14+) |
| UI looks small/cut off | Resize window - it's responsive |
| Config fields always locked | Check `_on_stop()` method calls `config(state="readonly")` |
| Status metrics show "N/A" | Normal - need backend to populate |

---

## 📁 File Location

```
AWH_dataextraction/
├── awh_ui_layout.py ← The main UI file (316 lines)
├── AquaPars1.py ← Integration point (uses awh_ui_layout)
└── UI_GUIDE.md ← Full documentation
```

---

## 🔧 Technical Details

| Aspect | Details |
|--------|---------|
| **Framework** | Tkinter (built-in) |
| **Python** | 3.14+ |
| **File Size** | 316 lines |
| **Dependencies** | None (tkinter is built-in) |
| **Layout** | Scrollable canvas with centered content |
| **State Machine** | 3-state FSM (STOPPED, VALIDATED, RUNNING) |
| **Responsiveness** | 900x700px (resizable, min 800x600px) |

---

## 📞 Need Help?

1. **Run the UI**: `python3 awh_ui_layout.py`
2. **Click VALIDATE**: See state change to 🟡
3. **Click START**: See state change to 🟢
4. **Click STOP**: See state change back to 🔴
5. **Try changing config**: See it lock when running

---

*Version: 1.0.0 - Production Ready*
*Last Updated: February 12, 2026*
