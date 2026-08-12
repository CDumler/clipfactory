import datetime
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

_requests_stub = types.ModuleType("requests")
_requests_stub.get = None
_requests_stub.post = None
sys.modules.setdefault("requests", _requests_stub)

from app import twitch


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TwitchDiscoveryMockTest(unittest.TestCase):
    def setUp(self):
        os.environ["TWITCH_CLIENT_ID"] = "x"
        os.environ["TWITCH_CLIENT_SECRET"] = "y"
        twitch._token["value"] = "dummy-token"
        twitch._token["exp"] = 10**12

        self._old_history_path = twitch.HISTORY_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        twitch.HISTORY_PATH = os.path.join(self._tmpdir.name, "viewer_history.json")
        with open(twitch.HISTORY_PATH, "w") as f:
            json.dump({
                "gamma": [3000, 3000, 3000, 3000],
                "delta": [2000, 2000, 2000, 2000],
            }, f)

    def tearDown(self):
        twitch.HISTORY_PATH = self._old_history_path
        self._tmpdir.cleanup()

    def _ts(self, hours_ago):
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - datetime.timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")

    def _fake_get(self, url, params=None, headers=None, timeout=None):
        self.assertEqual(headers["Client-ID"], "x")
        self.assertEqual(headers["Authorization"], "Bearer dummy-token")

        if url.endswith("/helix/users"):
            logins = [value for key, value in params if key == "login"]
            users = {
                "alpha": {"id": "1", "login": "alpha"},
                "beta": {"id": "2", "login": "beta"},
            }
            return _FakeResponse({
                "data": [users[login] for login in logins if login in users]
            })

        if url.endswith("/helix/streams"):
            self.assertEqual(params["language"], "de")
            return _FakeResponse({
                "data": [
                    {"user_id": "3", "user_login": "gamma", "viewer_count": 9000},
                    {"user_id": "4", "user_login": "delta", "viewer_count": 4000},
                    {"user_id": "1", "user_login": "alpha", "viewer_count": 3500},
                    {"user_id": "5", "user_login": "epsilon", "viewer_count": 2500},
                ],
                "pagination": {},
            })

        if url.endswith("/helix/clips"):
            clip_map = {
                "1": [{
                    "id": "clip-alpha",
                    "url": "https://clips.twitch.tv/alpha",
                    "title": "Alpha clip",
                    "view_count": 120,
                    "duration": 20,
                    "language": "de",
                    "created_at": self._ts(12),
                }],
                "2": [{
                    "id": "clip-beta",
                    "url": "https://clips.twitch.tv/beta",
                    "title": "Beta clip",
                    "view_count": 400,
                    "duration": 18,
                    "language": "de",
                    "created_at": self._ts(6),
                }],
                "3": [{
                    "id": "clip-gamma",
                    "url": "https://clips.twitch.tv/gamma",
                    "title": "Gamma clip",
                    "view_count": 1000,
                    "duration": 22,
                    "language": "de",
                    "created_at": self._ts(2),
                }],
            }
            return _FakeResponse({"data": clip_map[params["broadcaster_id"]]})

        raise AssertionError(f"unerwarteter GET-Aufruf: {url}")

    def test_curated_and_hype_discovery(self):
        cfg = {
            "profiles": {
                "de": {
                    "language": "de",
                    "streamers": ["alpha", "beta", "ghost"],
                    "hype_filter": {
                        "enabled": True,
                        "min_live_viewers": 3000,
                        "spike_ratio": 2.5,
                        "max_new_per_run": 5,
                    },
                    "clips_per_streamer": 2,
                    "candidates_target": 15,
                }
            },
            "facecams": {},
        }

        with patch("app.twitch.load_config", return_value=cfg), \
             patch("app.twitch.requests.get", side_effect=self._fake_get):
            clips, broadcaster_count, missing = twitch.discover_candidates("de")

        self.assertEqual(missing, ["ghost"])
        self.assertEqual(broadcaster_count, 3)
        self.assertEqual([c["streamer"] for c in clips], ["gamma", "beta", "alpha"])
        self.assertTrue(clips[0].get("hype"))
        self.assertFalse(clips[1].get("hype", False))
        self.assertEqual([c["id"] for c in clips], ["clip-gamma", "clip-beta", "clip-alpha"])

    def test_heat_score_prefers_real_momentum(self):
        now = datetime.datetime.now(datetime.timezone.utc)

        def raw_clip(views, hours_ago):
            return {
                "view_count": views,
                "created_at": (now - datetime.timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
            }

        solid = twitch.heat_score(raw_clip(20, 1))
        stale = twitch.heat_score(raw_clip(300, 48))
        too_fresh = twitch.heat_score(raw_clip(5, 0.05))

        self.assertGreater(solid["score"], stale["score"])
        self.assertGreater(solid["score"], too_fresh["score"])
        self.assertAlmostEqual(solid["velocity"], 20.0, places=1)
        self.assertAlmostEqual(too_fresh["alter_h"], 0.05, places=2)

    def test_center_mode_is_returned_as_marker(self):
        old_cfg_path = os.environ.get("DISCOVERY_CONFIG_PATH")
        cfg_path = os.path.join(self._tmpdir.name, "discovery.json")
        with open(cfg_path, "w") as f:
            json.dump({"profiles": {}, "facecams": {}}, f)
        os.environ["DISCOVERY_CONFIG_PATH"] = cfg_path
        try:
            twitch.save_facecam("alpha", "center")
            self.assertEqual(twitch.facecam_for("alpha"), "center")
            self.assertTrue(twitch.facecam_known("alpha"))
            self.assertTrue(twitch.reset_facecam("alpha"))
            self.assertIsNone(twitch.facecam_for("alpha"))
        finally:
            if old_cfg_path is None:
                os.environ.pop("DISCOVERY_CONFIG_PATH", None)
            else:
                os.environ["DISCOVERY_CONFIG_PATH"] = old_cfg_path


if __name__ == "__main__":
    unittest.main(verbosity=2)
