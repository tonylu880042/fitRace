"""Resource-aware Hyrox simulator (Phase 6a).

Loads a venue config, registers athletes, starts the race, then drives each
athlete through the full course by publishing node/antenna telemetry the same
way real edge hardware would:

  - entry-gate RFID tap  -> gym/telemetry/rfid/<node>   (binds an FTMS/rep unit)
  - FTMS distance        -> gym/telemetry/ftms/<node>   (treadmill/ski/row)
  - lane crossings       -> gym/telemetry/rfid/<node>   (alternating endpoints)
  - wall-ball reps       -> gym/telemetry/wallball/<node>

Each athlete is pinned to their own resource index so there is no contention;
realistic pooling/queueing is a later enhancement.
"""

import os
import json
import time
import urllib.request
from paho.mqtt import client as mqtt_client

MQTT_BROKER = os.environ.get("FITRACE_MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("FITRACE_MQTT_PORT", "1883"))
BASE_URL = os.environ.get("FITRACE_HYROX_API", "http://localhost:8000/api/hyrox")
ADMIN_TOKEN = os.environ.get("FITRACE_ADMIN_TOKEN", "")

N = 3  # athletes (and one dedicated resource of each type per athlete)

ATHLETES = [
    {"name": "Alex Rivera", "tag": "TAG_ALEX"},
    {"name": "Sarah Cheng", "tag": "TAG_SARAH"},
    {"name": "Diego Mora", "tag": "TAG_DIEGO"},
][:N]

# Course as (stage, kind) in order. kind drives which telemetry to emit.
STAGES = [
    ("run_1", "ftms"), ("ski_erg", "ftms"), ("run_2", "ftms"),
    ("sled_push", "lane"), ("run_3", "ftms"), ("sled_pull", "lane"),
    ("run_4", "ftms"), ("burpee_broad", "lane"), ("run_5", "ftms"),
    ("row", "ftms"), ("run_6", "ftms"), ("farmers_carry", "lane"),
    ("run_7", "ftms"), ("sandbag_lunges", "lane"), ("run_8", "ftms"),
    ("wall_balls", "reps"),
]


def _now_ms():
    return int(time.time() * 1000)


def api_post(path, payload=None):
    headers = {"Content-Type": "application/json"}
    if ADMIN_TOKEN:
        headers["X-FitRace-Admin-Token"] = ADMIN_TOKEN
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req)
        return True
    except Exception as e:
        print(f"POST {path} failed:", e)
        return False


def _ftms_unit(prefix, i, group_gate):
    return {
        "resource_id": f"{prefix}-{i}",
        "display_name": f"{prefix} {i}",
        "sensor_class": "ftms_machine",
        "node_id": f"edge-{prefix}-{i}",
        "entry_gate": {"node_id": f"rfid-{prefix}-{i}", "antenna_id": f"{group_gate}{i}_GATE"},
    }


def build_venue():
    groups = [
        {"group_id": "run_treadmills", "resource_type": "ftms_machine_pool", "stage_candidates": [],
         "units": [_ftms_unit("treadmill", i, "T") for i in range(1, N + 1)]},
        {"group_id": "ski_erg_pool", "resource_type": "ftms_machine_pool", "stage_candidates": [],
         "units": [_ftms_unit("ski", i, "S") for i in range(1, N + 1)]},
        {"group_id": "row_pool", "resource_type": "ftms_machine_pool", "stage_candidates": [],
         "units": [_ftms_unit("row", i, "R") for i in range(1, N + 1)]},
        {"group_id": "shared_turf_lanes", "resource_type": "rfid_lane_pool", "stage_candidates": [],
         "units": [{
             "resource_id": f"lane-{i}", "display_name": f"Lane {i}",
             "sensor_class": "rfid_endpoint_pair",
             "start_endpoint": {"node_id": f"rfid-lane-{i}", "antenna_id": f"L{i}_START"},
             "finish_endpoint": {"node_id": f"rfid-lane-{i}", "antenna_id": f"L{i}_FINISH"},
         } for i in range(1, N + 1)]},
        {"group_id": "wall_ball_targets", "resource_type": "rep_counter_pool", "stage_candidates": [],
         "units": [{
             "resource_id": f"wallball-{i}", "display_name": f"Wall Ball {i}",
             "sensor_class": "rep_counter", "node_id": f"edge-wb-{i}",
             "entry_gate": {"node_id": f"rfid-wb-{i}", "antenna_id": f"W{i}_GATE"},
         } for i in range(1, N + 1)]},
    ]
    return {"venue_id": "sim-hq", "course_profile_id": "hyrox_standard_2026",
            "resource_groups": groups}


class Sim:
    def __init__(self, client):
        self.client = client

    def rfid(self, node, antenna, tag):
        self.client.publish(f"gym/telemetry/rfid/{node}", json.dumps({
            "node_id": node, "antenna_id": antenna, "tag_id": tag,
            "rssi": -42.0, "timestamp_epoch_ms": _now_ms()}))
        time.sleep(0.02)

    def ftms(self, node, distance):
        self.client.publish(f"gym/telemetry/ftms/{node}", json.dumps({
            "node_id": node, "distance_m": distance, "timestamp_epoch_ms": _now_ms()}))
        time.sleep(0.02)

    def wallball(self, node):
        self.client.publish(f"gym/telemetry/wallball/{node}", json.dumps({
            "node_id": node, "timestamp_epoch_ms": _now_ms()}))
        time.sleep(0.005)

    def run_stage(self, ath, idx, stage, kind):
        i = idx + 1
        tag = ath["tag"]
        if kind == "ftms":
            prefix = "treadmill" if stage.startswith("run_") else ("ski" if stage == "ski_erg" else "row")
            gate = {"treadmill": "T", "ski": "S", "row": "R"}[prefix]
            self.rfid(f"rfid-{prefix}-{i}", f"{gate}{i}_GATE", tag)   # bind
            for d in (0, 400, 800, 1200):                            # -> 1000m target
                self.ftms(f"edge-{prefix}-{i}", d)
            print(f"  {ath['name']}: {stage} done ({prefix}-{i})")
        elif kind == "lane":
            node = f"rfid-lane-{i}"
            seq = ["L%d_START", "L%d_FINISH"] * 3  # position + 4 lengths + spare
            for k in range(5):
                self.rfid(node, (f"L{i}_START" if k % 2 == 0 else f"L{i}_FINISH"), tag)
            print(f"  {ath['name']}: {stage} done (lane-{i})")
        elif kind == "reps":
            self.rfid(f"rfid-wb-{i}", f"W{i}_GATE", tag)  # bind wall-ball target
            for _ in range(75):
                self.wallball(f"edge-wb-{i}")
            print(f"  {ath['name']}: wall_balls done (wallball-{i})")


def main():
    print("Connecting to MQTT...")
    client = mqtt_client.Client(callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
    try:
        client.connect(MQTT_BROKER, MQTT_PORT)
        client.loop_start()
    except Exception as e:
        print("MQTT connection failed:", e)
        return

    if not api_post("/venue-config", {"venue": build_venue(), "mode": "training"}):
        print("Could not load venue config; is Hyrox enabled and the token set?")
        return
    print(f"Venue loaded. Registering {N} athletes...")
    for a in ATHLETES:
        api_post("/register", {"athlete_name": a["name"], "rfid_tag_id": a["tag"],
                               "division": "individual"})
    api_post("/start")
    print("Race started.\n")

    sim = Sim(client)
    # Lockstep by stage so the board shows athletes progressing together.
    for stage, kind in STAGES:
        print(f"Stage: {stage}")
        for idx, ath in enumerate(ATHLETES):
            sim.run_stage(ath, idx, stage, kind)
        time.sleep(0.3)

    print("\nSimulation complete.")
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
