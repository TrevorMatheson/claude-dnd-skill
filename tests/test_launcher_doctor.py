"""The cross-platform launcher picks the real LAN adapter; the doctor reports.

* start-display.sh needed bash, python3 and macOS `ipconfig getifaddr`. On the
  test machine the first Windows adapter was VMware, which phones can't reach.
  start_display.py runs on sys.executable and skips virtual adapters.
* doctor.py checks the table setup and never changes a setting.
"""
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "dnd" / "display"))
sys.path.insert(0, str(REPO / "skills" / "dnd" / "scripts"))

import start_display as sd  # noqa: E402
import doctor  # noqa: E402


class TestLanIp(unittest.TestCase):

    def test_usable(self):
        self.assertTrue(sd._usable("192.168.1.20"))
        self.assertFalse(sd._usable("169.254.3.4"))     # link-local
        self.assertFalse(sd._usable("127.0.0.1"))
        self.assertFalse(sd._usable("8.8.8.8"))         # not a LAN address
        self.assertFalse(sd._usable("nonsense"))

    def test_skips_vmware_on_windows(self):
        adapters = [("Ethernet adapter VMware Network Adapter VMnet1", "192.168.223.1"),
                    ("Wireless LAN adapter Wi-Fi", "192.168.88.19")]
        with mock.patch.object(sd.os, "name", "nt"), \
                mock.patch.object(sd, "_windows_adapters", return_value=adapters), \
                mock.patch.object(sd, "_route_ip", return_value=None):
            self.assertEqual(sd.lan_ip(), "192.168.88.19")

    def test_routed_address_preferred_when_it_is_a_real_adapter(self):
        adapters = [("Ethernet adapter Ethernet", "10.0.0.5"), ("Wireless LAN adapter Wi-Fi", "192.168.1.9")]
        with mock.patch.object(sd.os, "name", "nt"), \
                mock.patch.object(sd, "_windows_adapters", return_value=adapters), \
                mock.patch.object(sd, "_route_ip", return_value="192.168.1.9"):
            self.assertEqual(sd.lan_ip(), "192.168.1.9")

    def test_routed_virtual_address_not_chosen(self):
        adapters = [("Ethernet adapter vEthernet (WSL)", "172.20.0.1"), ("Ethernet adapter Ethernet", "192.168.0.4")]
        with mock.patch.object(sd.os, "name", "nt"), \
                mock.patch.object(sd, "_windows_adapters", return_value=adapters), \
                mock.patch.object(sd, "_route_ip", return_value="172.20.0.1"):
            self.assertEqual(sd.lan_ip(), "192.168.0.4")


class TestDoctor(unittest.TestCase):

    def test_runs_offline_and_reports_each_area(self):
        with mock.patch.object(doctor, "_get_json", return_value=None), \
                mock.patch.object(doctor.shutil, "which", return_value=None), \
                mock.patch.object(doctor, "check_firewall"):
            results = doctor.run()
        names = [r["check"] for r in results]
        for want in ("Python", "package flask", "SRD 2014", "SRD 2024", "display (localhost)",
                     "voice key", "claude CLI"):
            self.assertIn(want, names)
        disp = [r for r in results if r["check"] == "display (localhost)"][0]
        self.assertEqual(disp["status"], doctor.WARN)
        self.assertIn("start_display.py", disp["fix"])

    def test_firewall_fix_is_shown_not_run(self):
        src = (REPO / "skills" / "dnd" / "scripts" / "doctor.py").read_text(encoding="utf-8")
        self.assertNotIn('"add", "rule"', src)          # never adds the rule itself
        self.assertIn("in an administrator terminal", src)


if __name__ == "__main__":
    unittest.main()
