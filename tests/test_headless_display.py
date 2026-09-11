import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pygame

import marqueemark


class HeadlessDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.art_dir = Path(self.temp_dir.name)
        (self.art_dir / "generic.png").write_bytes(b"display conversion must be deferred")

        display = object.__new__(marqueemark.Display)
        display.art_dir = str(self.art_dir)
        display.layout_mode = "mini"
        display.electrocoin = False
        display.headless = True
        display.screen = pygame.Surface((1024, 768))
        display.phys = (1024, 768)
        display.size = (768, 1024)
        display.rect = pygame.Rect(0, 0, 768, 1024)
        display.rotate = 90
        display.dpad_offset = 0
        display.tilt = 0.0
        display.calibrating = False
        display.cal_work = None
        display.current = None
        display.last_game = None
        display.restore_callback = None
        display.electro_neosd = None
        display.electro_config = marqueemark.electro_config()
        display.manual_sleep = False
        display.wake_count = 0
        display._headless_retry_at = 0
        self.display = display

    @mock.patch.object(marqueemark, "load_electrocoin_config")
    @mock.patch.object(marqueemark.pygame.display, "set_mode",
                       side_effect=pygame.error("forced headless test"))
    def test_constructed_display_accepts_saved_mode_headless(self, set_mode, load_config):
        load_config.return_value = marqueemark.electro_config()
        display = marqueemark.Display(str(self.art_dir), rotate=90, layout_mode="mini")
        self.assertTrue(display.headless)
        self.assertTrue(display.set_layout_mode("mini"))
        self.assertIsNone(display.last_game)

    @mock.patch.object(marqueemark, "load_electrocoin_config")
    @mock.patch.object(marqueemark.pygame.display, "set_mode",
                       return_value=pygame.Surface((1366, 768)))
    def test_saved_ultrawide_mode_starts_without_portrait_rotation(self, set_mode,
                                                                   load_config):
        load_config.return_value = marqueemark.electro_config()
        with mock.patch.object(marqueemark.pygame.display, "flip"):
            display = marqueemark.Display(str(self.art_dir), rotate=90,
                                          layout_mode="ultrawide")
        self.assertTrue(display.electrocoin)
        self.assertEqual(display.rotate, 0)
        self.assertEqual(display.size, (1366, 768))

    @mock.patch.object(marqueemark.pygame.image, "load")
    def test_saved_mini_mode_does_not_decode_art_headless(self, load):
        self.assertTrue(self.display.set_layout_mode("mini"))
        load.assert_not_called()

    @mock.patch.object(marqueemark, "load_electrocoin_config")
    @mock.patch.object(marqueemark.pygame.image, "load")
    def test_admin_can_select_ultrawide_headless(self, load, load_config):
        load_config.return_value = marqueemark.electro_config()
        self.assertTrue(self.display.set_layout_mode("ultrawide"))
        self.assertEqual(self.display.layout_mode, "ultrawide")
        load.assert_not_called()

    @mock.patch.object(marqueemark.pygame.image, "load")
    def test_game_state_is_retained_headless(self, load):
        game = {"short": "mslug", "title": "Metal Slug", "ngh": "201"}
        self.display.show_game(game)
        self.assertEqual(self.display.last_game, game)
        load.assert_not_called()

    @mock.patch.object(marqueemark.pygame.image, "load")
    def test_calibration_art_preview_is_deferred_headless(self, load):
        self.display.calibrating = True
        self.display.cal_work = {"x": 0, "y": 0, "w": 100, "h": 100,
                                 "tilt": 0, "rotate": 90, "preview": "art"}
        self.display._cal_sample = None
        self.display._render_calibration_frame()
        load.assert_not_called()

    def test_hdmi_reacquisition_redraws_retained_game(self):
        game = {"short": "mslug", "title": "Metal Slug", "ngh": "201"}
        self.display.last_game = game
        with mock.patch.object(marqueemark.pygame.display, "init"), \
             mock.patch.object(marqueemark.pygame.mouse, "set_visible"), \
             mock.patch.object(marqueemark.pygame.display, "set_mode",
                               return_value=pygame.Surface((1366, 768))), \
             mock.patch.object(self.display, "show_game") as show_game:
            self.display._retry_headless_display()
        self.assertFalse(self.display.headless)
        show_game.assert_called_once_with(game)

    @mock.patch.object(marqueemark, "load_electrocoin_config")
    def test_real_display_size_replaces_headless_ultrawide_canvas(self, load_config):
        load_config.return_value = marqueemark.electro_config()
        self.display.set_layout_mode("ultrawide")
        with mock.patch.object(marqueemark.pygame.display, "init"), \
             mock.patch.object(marqueemark.pygame.mouse, "set_visible"), \
             mock.patch.object(marqueemark.pygame.display, "set_mode",
                               return_value=pygame.Surface((1366, 768))), \
             mock.patch.object(self.display, "show_idle"):
            self.display._retry_headless_display()
        self.assertEqual(self.display.size, (1366, 768))
        self.assertEqual(self.display.rect, pygame.Rect(0, 0, 1366, 768))

    def test_machine_shutdown_sequence_finishes_black(self):
        self.display._shutdown_preview = {"started": 10, "seconds": 10,
                                          "restore": False}
        with mock.patch.object(marqueemark.time, "monotonic", return_value=20), \
             mock.patch.object(self.display, "blank") as blank, \
             mock.patch.object(self.display, "show_idle") as show_idle:
            self.display._update_shutdown_preview()
        blank.assert_called_once_with()
        show_idle.assert_not_called()

    def test_machine_shutdown_signal_queues_renderer_work(self):
        while not marqueemark.DISPLAY_QUEUE.empty():
            marqueemark.DISPLAY_QUEUE.get_nowait()
        marqueemark.queue_shutdown_sequence()
        self.assertEqual(marqueemark.DISPLAY_QUEUE.get_nowait(),
                         ("shutdown_sequence",))

    def test_cabinet_poweroff_marks_completed_animation(self):
        marker = mock.Mock()
        with mock.patch.object(marqueemark, "CABINET_POWEROFF_MARKER", marker), \
             mock.patch.object(marqueemark.subprocess, "run"), \
             mock.patch.object(marqueemark.subprocess, "Popen") as popen:
            marqueemark._poweroff_pi()
        marker.touch.assert_called_once_with()
        popen.assert_called_once_with(
            ["sudo", "-n", "/usr/bin/systemctl", "poweroff"],
            stdout=marqueemark.subprocess.DEVNULL,
            stderr=marqueemark.subprocess.DEVNULL)

    def test_native_startup_sequence_is_deferred_headless(self):
        self.assertFalse(self.display.show_startup_splash())


if __name__ == "__main__":
    unittest.main()
