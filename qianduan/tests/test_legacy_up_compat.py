"""Regression tests for requests sent by the unmodified up/houduan/core.py."""

import io
import tempfile
import unittest
from pathlib import Path

from server.app import create_app


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_IMAGE = ROOT / "tests/fixtures/legacy_damage.jpg"


class LegacyUpCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "DATA_DIR": self.tempdir.name,
            "DEVICE_TOKEN": "",
            "CONTROL_TOKEN": "",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def _legacy_heartbeat(self, with_frame=False):
        form = {"latitude": "43.846764", "longitude": "87.731124"}
        if with_frame:
            form["frame"] = (io.BytesIO(EXAMPLE_IMAGE.read_bytes()), "heartbeat.jpg")
        return self.client.post("/update_stream", data=form)

    def test_old_device_8080_root_url_stays_root(self):
        self.assertEqual(self._legacy_heartbeat().status_code, 200)
        report = self.client.post(
            "/api/report_pi_url", json={"url": "https://192.168.10.5:8080"}
        )
        self.assertEqual(report.status_code, 200)
        data = self.client.get("/api/mobile_access").get_json()
        self.assertEqual(data["entry_url"], "https://192.168.10.5:8080/")
        self.assertTrue(data["ready"])

    def test_new_device_phone_url_is_not_rewritten(self):
        self.assertEqual(self._legacy_heartbeat().status_code, 200)
        report = self.client.post(
            "/api/report_pi_url", json={"url": "https://192.168.10.5:8080/phone"}
        )
        self.assertEqual(report.status_code, 200)
        data = self.client.get("/api/mobile_access").get_json()
        self.assertEqual(data["entry_url"], "https://192.168.10.5:8080/phone")

    def test_old_heartbeat_and_control_commands(self):
        self.assertEqual(self._legacy_heartbeat(with_frame=True).status_code, 200)
        status = self.client.get("/api/status").get_json()
        self.assertTrue(status["online"])
        video = self.client.get("/api/video_status").get_json()
        self.assertTrue(video["cloud_available"])
        self.assertGreater(video["frames_received"], 0)

        control = self.client.post("/api/control", json={"command": "START"})
        self.assertEqual(control.status_code, 200)
        command = self.client.get("/api/command").get_json()
        self.assertEqual(command["command"], "RUNNING")
        self.assertTrue(command["session_id"].startswith("Task_"))

    def test_old_upload_fields_preserve_record_and_image(self):
        response = self.client.post("/upload", data={
            "latitude": "43.846764",
            "longitude": "87.731124",
            "severity": "2",
            "session_id": "Task_Legacy",
            "timestamp": "1761360000",
            "image": (io.BytesIO(EXAMPLE_IMAGE.read_bytes()), "damage.jpg"),
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        record = self.client.get("/api/records").get_json()[0]
        self.assertEqual(record["level"], "中度")
        self.assertEqual(record["session_id"], "Task_Legacy")
        self.assertAlmostEqual(record["lat"], 43.846764)
        self.assertAlmostEqual(record["lon"], 87.731124)
        image_response = self.client.get("/images/" + record["image"])
        self.assertEqual(image_response.status_code, 200)
        image_response.close()


if __name__ == "__main__":
    unittest.main()
