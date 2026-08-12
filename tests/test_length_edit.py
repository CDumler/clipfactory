import sys
import types
import unittest

_requests_stub = types.ModuleType("requests")
_requests_stub.get = None
_requests_stub.post = None
sys.modules.setdefault("requests", _requests_stub)

_fastapi_stub = types.ModuleType("fastapi")

class _FakeFastAPI:
    def __init__(self, *args, **kwargs):
        pass

    def get(self, *args, **kwargs):
        return lambda fn: fn

    def post(self, *args, **kwargs):
        return lambda fn: fn

    def on_event(self, *args, **kwargs):
        return lambda fn: fn

_fastapi_stub.FastAPI = _FakeFastAPI
sys.modules.setdefault("fastapi", _fastapi_stub)

_fastapi_responses_stub = types.ModuleType("fastapi.responses")
_fastapi_responses_stub.FileResponse = object
_fastapi_responses_stub.JSONResponse = object
sys.modules.setdefault("fastapi.responses", _fastapi_responses_stub)

_apscheduler_stub = types.ModuleType("apscheduler")
_apscheduler_schedulers_stub = types.ModuleType("apscheduler.schedulers")
_apscheduler_bg_stub = types.ModuleType("apscheduler.schedulers.background")

class _FakeScheduler:
    def __init__(self, *args, **kwargs):
        pass

    def add_job(self, *args, **kwargs):
        return None

    def start(self):
        return None

_apscheduler_bg_stub.BackgroundScheduler = _FakeScheduler
sys.modules.setdefault("apscheduler", _apscheduler_stub)
sys.modules.setdefault("apscheduler.schedulers", _apscheduler_schedulers_stub)
sys.modules.setdefault("apscheduler.schedulers.background", _apscheduler_bg_stub)

from app import main


class LengthEditParseTest(unittest.TestCase):
    def setUp(self):
        self.clip = {"start_s": 5, "end_s": 30, "duration": 60}

    def test_absolute_range(self):
        parsed, err = main._parse_length_edit("10-25", self.clip)
        self.assertIsNone(err)
        self.assertEqual(parsed, {"start_s": 10.0, "end_s": 25.0})

    def test_shift_start(self):
        parsed, err = main._parse_length_edit("+2", self.clip)
        self.assertIsNone(err)
        self.assertEqual(parsed, {"start_s": 7.0, "end_s": 30.0})

    def test_shift_end(self):
        parsed, err = main._parse_length_edit("e-3", self.clip)
        self.assertIsNone(err)
        self.assertEqual(parsed, {"start_s": 5.0, "end_s": 27.0})

    def test_rejects_too_short_range(self):
        parsed, err = main._parse_length_edit("10-12", self.clip)
        self.assertIsNone(parsed)
        self.assertIn("mindestens 5 Sekunden", err)

    def test_schedule_slots_accepts_none_for_manual_upload(self):
        self.assertEqual(main._schedule_slots("none", "17:00"), [])
        self.assertEqual(main._schedule_slots("manual", "17:00"), [])

    def test_schedule_slots_parses_multiple_times(self):
        self.assertEqual(main._schedule_slots("10:00, 18:30", "17:00"), [(10, 0), (18, 30)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
