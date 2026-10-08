import unittest
from datetime import datetime, timedelta, timezone

from app.automation_engine import evaluate


NOW = datetime(2026, 10, 8, 19, 0, tzinfo=timezone.utc)


class AutomationEngineTests(unittest.TestCase):
    def test_evening_arrival_turns_on_light(self):
        result = evaluate({
            "room": "living_room",
            "observed_at": NOW,
            "observations": {"occupied": True, "light_level_lux": 35, "local_hour": 19},
        }, now=NOW)
        self.assertEqual(result["matched_rules"][0]["id"], "evening-arrival-light")
        self.assertEqual(result["selected_actions"][0]["value"], "on")

    def test_energy_rule_only_targets_approved_devices(self):
        result = evaluate({
            "observed_at": NOW,
            "observations": {"occupied": False, "away_mode": True, "home_power_w": 1600},
            "approved_standby_devices": ["television", "desk lamp"],
        }, now=NOW)
        self.assertEqual({a["target"] for a in result["selected_actions"]},
                         {"television", "desk lamp"})

    def test_safety_suppresses_lower_priority_automation(self):
        result = evaluate({
            "observed_at": NOW,
            "observations": {"occupied": True, "light_level_lux": 20, "local_hour": 19,
                             "away_mode": True, "door_open": True,
                             "authorized_entry": False},
        }, now=NOW)
        self.assertEqual(result["selected_actions"][0]["priority"], "safety")
        self.assertEqual(result["rejected_actions"][0]["rule_id"], "evening-arrival-light")

    def test_resident_override_beats_comfort_rule(self):
        result = evaluate({
            "room": "living_room",
            "observed_at": NOW,
            "observations": {"occupied": True, "light_level_lux": 20, "local_hour": 19},
            "resident_command": {"device": "living room light", "state": "off",
                                 "hold_minutes": 30},
        }, now=NOW)
        self.assertEqual(result["selected_actions"][0]["rule_id"], "resident-override")
        self.assertEqual(result["rejected_actions"][0]["rule_id"], "evening-arrival-light")

    def test_stale_and_private_occupancy_do_not_match(self):
        stale = evaluate({
            "observed_at": NOW - timedelta(minutes=10),
            "observations": {"occupied": True, "light_level_lux": 20, "local_hour": 19},
        }, now=NOW)
        private = evaluate({
            "observed_at": NOW,
            "occupancy_automation": False,
            "observations": {"occupied": True, "light_level_lux": 20, "local_hour": 19},
        }, now=NOW)
        self.assertFalse(stale["matched_rules"])
        self.assertFalse(private["matched_rules"])
        self.assertNotIn("occupied", {fact["name"] for fact in private["facts"]})


if __name__ == "__main__":
    unittest.main()
