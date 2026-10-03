"""Wiring smoke test for AquaPars1.py and AquaPars1_new_pm.py.

Hardware modules (serial, GPIO, Tk) are replaced with stubs so the real
StationController runs on any machine. Verifies the uploader is created, fed the
same payload the old send_to_cloud() sent, and started/stopped with the station.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloud_uploader as cu  # noqa: E402

# The 17 fields the old send_to_cloud() put in its payload (plus station_name).
EXPECTED_FIELDS = {
    "temperature", "humidity", "velocity", "unit",
    "outtake_temperature", "outtake_humidity", "outtake_velocity", "outtake_unit",
    "voltage", "current", "power", "energy", "weight", "pump_status",
    "flow_lmin", "flow_hz", "flow_total",
}


class StubReader:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class StubPump:
    is_on = False

    def set_status_callback(self, cb):
        pass

    def auto_check(self, weight):
        pass

    def cleanup(self):
        pass


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class FakePost:
    def __init__(self):
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append(json)
        return types.SimpleNamespace(status_code=200, text="ok")


@pytest.fixture(params=["AquaPars1", "AquaPars1_new_pm"])
def station(request, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # station_state/ and measure_data/ are cwd-relative
    stubs = {
        "intake_anemometer": stub("intake_anemometer", intake_anemometer=lambda: (50.0, 25.0, 1.2, "m/s")),
        "outtake_anemometer": stub("outtake_anemometer", outtake_anemometer=lambda: (60.0, 30.0, 0.8, "m/s")),
        "pump_controller": stub("pump_controller", PumpController=StubPump),
        "read_balance": stub("read_balance", BalanceSerialReader=StubReader,
                             parse_balance_line=lambda line: ("ST", "GS", "", 12.5, "g")),
        "read_power": stub("read_power", PowerMeterReader=StubReader),
        "read_power_new": stub("read_power_new", PowerMeterReader=StubReader),
        "read_flow": stub("read_flow", FlowMeterReader=StubReader),
        "awh_ui_layout": stub("awh_ui_layout", AWHControlPanel=object),
    }
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, request.param, raising=False)
    module = importlib.import_module(request.param)

    controller = module.StationController(None, str(tmp_path / "measure_data"), None, StubPump())
    controller.uploader._post = FakePost()
    controller.uploader._clock_synced = lambda: True
    controller.start_time = __import__("time").time()  # normally set by start_reading()
    yield module, controller
    controller.running = False
    controller.uploader.stop()
    try:
        controller.csv_file.close()
    except Exception:
        pass
    sys.modules.pop(request.param, None)


def test_uploader_is_created_and_old_function_removed(station):
    module, controller = station
    assert isinstance(controller.uploader, cu.CloudUploader)
    assert not hasattr(module, "send_to_cloud")
    assert controller.uploader.url == module.CLOUD_URL


def test_save_data_queues_the_same_payload_the_old_code_sent(station):
    module, controller = station
    controller.balance_line = ("ST", "GS", "", 12.5, "g")
    controller.power_tuple = (230.0, 1.5, 345.0, 99.0)
    controller._last_power_ts = __import__("time").time()
    controller.flow_tuple = (0.4, 12.0, 3.3)
    controller.intake_air_data = (25.0, 1.2, 50.0, "m/s")
    controller.outtake_air_data = (30.0, 0.8, 60.0, "m/s")

    controller.save_data()

    assert len(controller.uploader.queue) == 1
    assert controller.uploader.attempt_next() == "sent"
    (body,) = controller.uploader._post.calls
    assert set(body) == EXPECTED_FIELDS | {"station_name", "reading_id"}
    assert body["station_name"] == module.STATION_NAME
    assert body["weight"] == 12.5 and body["power"] == 345.0 and body["flow_total"] == 3.3
    assert body["temperature"] == 25.0 and body["outtake_humidity"] == 60.0 and body["pump_status"] == 0
    assert "replayed" not in body and "client_timestamp" not in body  # live path unchanged


def test_uploads_still_limited_to_once_per_interval(station):
    _, controller = station
    controller.save_data()
    controller.save_data()
    controller.save_data()
    assert len(controller.uploader.queue) == 1


def test_missing_sensors_still_queue_with_none_values(station):
    _, controller = station  # no sensor data received yet
    controller.save_data()
    controller.uploader.attempt_next()
    (body,) = controller.uploader._post.calls
    assert body["weight"] is None and body["power"] is None and body["flow_lmin"] is None


def test_station_name_set_from_ui_is_used(station):
    _, controller = station
    controller.set_station_name("station_renamed")
    controller.save_data()
    controller.uploader.attempt_next()
    assert controller.uploader._post.calls[0]["station_name"] == "station_renamed"


def test_outage_does_not_stall_the_save_loop(station):
    import time
    _, controller = station

    def hanging(url, json=None, timeout=None):
        time.sleep(1.5)

    controller.uploader._post = hanging
    controller.uploader.start()
    controller.save_data()                 # queued; uploader thread now hangs inside post()
    time.sleep(0.1)
    controller._last_cloud_upload_ts = 0.0  # force another due upload
    t0 = time.time()
    controller.save_data()
    assert time.time() - t0 < 1.0          # the old code would block here for up to 10 s
    controller.uploader._stop.set()


def test_start_and_stop_reading_manage_the_uploader(station):
    _, controller = station
    controller.start_reading()
    assert controller.uploader._thread.is_alive()
    controller.stop_reading()
    controller.uploader._thread.join(5)
    assert not controller.uploader._thread.is_alive()
