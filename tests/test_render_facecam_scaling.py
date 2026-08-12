import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app import render


class RenderFacecamScalingTest(unittest.TestCase):
    def test_scale_facecam_box_from_reference_space(self):
        scaled = render._scale_facecam_box(
            {"x": 1440, "y": 60, "w": 420, "h": 240},
            1280,
            720,
        )
        self.assertEqual(scaled, {"x": 960, "y": 40, "w": 280, "h": 160})

    def test_scale_facecam_box_clamps_to_source_bounds(self):
        scaled = render._scale_facecam_box(
            {"x": 1800, "y": 980, "w": 400, "h": 200},
            1280,
            720,
        )
        self.assertGreaterEqual(scaled["x"], 0)
        self.assertGreaterEqual(scaled["y"], 0)
        self.assertGreaterEqual(scaled["w"], 2)
        self.assertGreaterEqual(scaled["h"], 2)
        self.assertLessEqual(scaled["x"] + scaled["w"], 1280)
        self.assertLessEqual(scaled["y"] + scaled["h"], 720)

    def test_pad_box_adds_small_air_around_facecam(self):
        expanded = render._pad_box(
            {"x": 960, "y": 40, "w": 280, "h": 160},
            1280,
            720,
            pad=render.FACECAM_PAD,
        )
        self.assertLessEqual(expanded["x"], 960)
        self.assertLessEqual(expanded["y"], 40)
        self.assertGreaterEqual(expanded["x"] + expanded["w"], 960 + 280)
        self.assertGreaterEqual(expanded["y"] + expanded["h"], 40 + 160)
        self.assertEqual(expanded["w"], 291)
        self.assertEqual(expanded["h"], 166)

    def test_smart_game_box_shifts_away_from_facecam(self):
        box = render._smart_game_box(
            1280,
            720,
            {"x": 960, "y": 40, "w": 280, "h": 160},
            dict(render.DEFAULT_LAYOUT),
        )
        self.assertEqual(box["w"], 589)
        self.assertEqual(box["h"], 720)
        self.assertLess(box["x"], 346)

    def test_render_uses_scaled_facecam_box_in_ffmpeg_filter(self):
        clip = {
            "id": "demo123",
            "start_s": 0,
            "end_s": 10,
            "duration": 10,
        }
        facecam = {"x": 1440, "y": 60, "w": 420, "h": 240}
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[0] == "ffprobe":
                return SimpleNamespace(stdout="1280x720\n")
            return SimpleNamespace(stdout="", returncode=0)

        with patch("app.render.subprocess.run", side_effect=fake_run):
            out = render.render(clip, facecam, "/tmp/demo.ass")

        self.assertEqual(out, "/data/out/demo123.mp4")
        ffmpeg_cmd = calls[-1][0]
        fc = ffmpeg_cmd[ffmpeg_cmd.index("-filter_complex") + 1]
        self.assertIn("crop=260:148:970:46,", fc)
        self.assertIn("scale=1080:600:force_original_aspect_ratio=increase,", fc)
        self.assertIn("crop=1080:600:(iw-1080)/2:(ih-600)/2[face]", fc)
        self.assertIn("crop=589:720:172:0,", fc)
        self.assertIn("scale=1080:1320[game]", fc)
        self.assertNotIn("boxblur=", fc)
        self.assertIn("[face][game]vstack[v0]", fc)
        self.assertNotIn("subtitles=/tmp/demo.ass", fc)

    def test_render_applies_manual_layout_offsets(self):
        clip = {
            "id": "demo123",
            "start_s": 0,
            "end_s": 10,
            "duration": 10,
            "layout_json": '{"cam_shift_x": 120, "game_shift_x": -160, "game_zoom": 1.2}',
        }
        facecam = {"x": 1440, "y": 60, "w": 420, "h": 240}
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[0] == "ffprobe":
                return SimpleNamespace(stdout="1280x720\n")
            return SimpleNamespace(stdout="", returncode=0)

        with patch("app.render.subprocess.run", side_effect=fake_run):
            render.render(clip, facecam, "/tmp/demo.ass")

        ffmpeg_cmd = calls[-1][0]
        fc = ffmpeg_cmd[ffmpeg_cmd.index("-filter_complex") + 1]
        self.assertIn("crop=260:148:1020:46,", fc)
        self.assertIn("crop=491:600:89:78,", fc)

    def test_render_center_mode_uses_full_frame_fit(self):
        clip = {
            "id": "demo123",
            "start_s": 0,
            "end_s": 10,
            "duration": 10,
        }
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return SimpleNamespace(stdout="", returncode=0)

        with patch("app.render.subprocess.run", side_effect=fake_run):
            render.render(clip, "center", "/tmp/demo.ass")

        ffmpeg_cmd = calls[-1][0]
        fc = ffmpeg_cmd[ffmpeg_cmd.index("-filter_complex") + 1]
        self.assertIn(
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black[v0]",
            fc,
        )
        self.assertNotIn("[face][game]vstack[v0]", fc)

    def test_render_only_burns_generated_captions_when_enabled(self):
        clip = {
            "id": "demo123",
            "start_s": 0,
            "end_s": 10,
            "duration": 10,
        }
        facecam = {"x": 1440, "y": 60, "w": 420, "h": 240}
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[0] == "ffprobe":
                return SimpleNamespace(stdout="1280x720\n")
            return SimpleNamespace(stdout="", returncode=0)

        with patch.dict("os.environ", {"ENABLE_GENERATED_CAPTIONS": "1"}, clear=False):
            with patch("app.render.subprocess.run", side_effect=fake_run):
                render.render(clip, facecam, "/tmp/demo.ass")

        ffmpeg_cmd = calls[-1][0]
        fc = ffmpeg_cmd[ffmpeg_cmd.index("-filter_complex") + 1]
        self.assertIn("subtitles=/tmp/demo.ass", fc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
