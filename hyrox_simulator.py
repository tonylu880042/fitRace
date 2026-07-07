import os
import time
import json
import random
import urllib.request
from paho.mqtt import client as mqtt_client

# MQTT Configuration
MQTT_BROKER = os.environ.get("FITRACE_MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("FITRACE_MQTT_PORT", "1883"))
RFID_TOPIC = "gym/telemetry/rfid/simulator"
WALLBALL_TOPIC = "gym/telemetry/wallball/simulator"

# API Configuration
BASE_URL = os.environ.get("FITRACE_HYROX_API", "http://localhost:8000/api/hyrox")
ADMIN_TOKEN = os.environ.get("FITRACE_ADMIN_TOKEN", "")
RSSI_THRESHOLD_DBM = float(os.environ.get("FITRACE_HYROX_SIM_RSSI_THRESHOLD_DBM", "-60"))
RUN_LAP_DEBOUNCE_MS = int(os.environ.get("FITRACE_HYROX_SIM_RUN_DEBOUNCE_MS", "10"))
API_READY_TIMEOUT_SEC = float(os.environ.get("FITRACE_HYROX_SIM_API_TIMEOUT_SEC", "15"))

STAGES = [
    {"type": "run", "name": "run_1"},
    {"type": "station", "name": "ski_erg", "station": 1},
    {"type": "run", "name": "run_2"},
    {"type": "lengths", "name": "sled_push", "station": 2, "lengths": 4},
    {"type": "run", "name": "run_3"},
    {"type": "lengths", "name": "sled_pull", "station": 3, "lengths": 4},
    {"type": "run", "name": "run_4"},
    {"type": "lengths", "name": "burpee_broad", "station": 4, "lengths": 4},
    {"type": "run", "name": "run_5"},
    {"type": "station", "name": "row", "station": 5},
    {"type": "run", "name": "run_6"},
    {"type": "lengths", "name": "farmers_carry", "station": 6, "lengths": 4},
    {"type": "run", "name": "run_7"},
    {"type": "lengths", "name": "sandbag_lunges", "station": 7, "lengths": 4},
    {"type": "run", "name": "run_8"},
    {"type": "wallballs", "name": "wall_balls", "station": 8, "reps": 75},
    {"type": "finished", "name": "finished"}
]

class SimulatedAthlete:
    def __init__(self, name, tag_id, division, team_name):
        self.name = name
        self.tag_id = tag_id
        self.division = division
        self.team_name = team_name
        self.stage_idx = 0
        self.lap_count = 0
        self.last_location = None
        self.last_action_time = 0
        self.is_finished = False


def rfid_payload(athlete, location, station_number, timestamp_ms, rssi=-42.0):
    return {
        "node_id": f"hyrox-sim-rfid-{station_number:02d}",
        "edge_node_id": "hyrox-simulator",
        "equipment_type": "rfid_timing_mat",
        "tag_id": athlete.tag_id,
        "location": location,
        "rssi": rssi,
        "timestamp_epoch_ms": timestamp_ms,
        "station_number": station_number,
    }


def wallball_payload(station_number, timestamp_ms):
    return {
        "node_id": f"hyrox-sim-wallball-{station_number:02d}",
        "equipment_type": "wallball_sensor",
        "station_number": station_number,
        "event": "valid_rep",
        "timestamp_epoch_ms": timestamp_ms,
    }


def api_post(path, payload=None):
    headers = {"Content-Type": "application/json"}
    if ADMIN_TOKEN:
        headers["X-FitRace-Admin-Token"] = ADMIN_TOKEN
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception as e:
        print(f"POST {path} failed:", e)
        return False


def wait_for_api():
    deadline = time.time() + API_READY_TIMEOUT_SEC
    url = f"{BASE_URL}/state"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as res:
                if res.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    print(f"Hyrox API is not ready after {API_READY_TIMEOUT_SEC:.1f}s: {url}")
    return False


def register_athletes(athletes):
    # configure resets backend state, then register every simulated tag —
    # unregistered tags are silently ignored by the backend
    configured = api_post(
        "/configure",
        {
            "competition_mode": "individual",
            "session_type": "training",
            "rssi_threshold_dbm": RSSI_THRESHOLD_DBM,
            # The simulator emits laps much faster than a real athlete would.
            # Keep the backend debounce low so simulated run laps are not dropped.
            "run_lap_debounce_ms": RUN_LAP_DEBOUNCE_MS,
        },
    )
    if not configured:
        return False

    for a in athletes:
        ok = api_post("/register", {
            "athlete_name": a.name,
            "rfid_tag_id": a.tag_id,
            "team_name": a.team_name,
            "division": a.division,
        })
        if ok:
            print(f"📝 Registered {a.name} ({a.tag_id})")
        else:
            return False
    return True

def start_race():
    if api_post("/start"):
        print("🚀 Hyrox Race Started!")
        return True
    return False

def force_complete_stage(tag_id):
    return api_post("/complete-stage", {"rfid_tag_id": tag_id})

def main():
    print("Connecting to MQTT broker...")
    client = mqtt_client.Client(callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
    try:
        client.connect(MQTT_BROKER, MQTT_PORT)
        client.loop_start()
        print("Connected to MQTT Broker!")
    except Exception as e:
        print("MQTT Connection failed. Make sure mosquitto is running on localhost:1883:", e)
        return

    # Initialize Athletes
    athletes = [
        # Individuals
        SimulatedAthlete("Individual Alex", "EPC_IND_ALEX", "individual", None),
        SimulatedAthlete("Individual Bella", "EPC_IND_BELLA", "individual", None),
        SimulatedAthlete("Individual Chris", "EPC_IND_CHRIS", "individual", None),
        SimulatedAthlete("Individual Diana", "EPC_IND_DIANA", "individual", None),
        # Doubles Team
        SimulatedAthlete("Alpha Tom", "EPC_DBL_TOM", "doubles", "Team Alpha"),
        SimulatedAthlete("Alpha Jerry", "EPC_DBL_JERRY", "doubles", "Team Alpha"),
        # Relay Team
        SimulatedAthlete("Relay Alice", "EPC_RLY_ALICE", "relay", "Fast Foursome"),
        SimulatedAthlete("Relay Bob", "EPC_RLY_BOB", "relay", "Fast Foursome"),
        SimulatedAthlete("Relay Charlie", "EPC_RLY_CHARLIE", "relay", "Fast Foursome"),
        SimulatedAthlete("Relay David", "EPC_RLY_DAVID", "relay", "Fast Foursome"),
    ]

    if not wait_for_api():
        client.loop_stop()
        client.disconnect()
        return

    if not register_athletes(athletes):
        client.loop_stop()
        client.disconnect()
        return

    if not start_race():
        client.loop_stop()
        client.disconnect()
        return

    time.sleep(1)

    print("\n--- Starting Race Simulation in Real-time ---")
    active_athletes = [a for a in athletes]
    wallball_occupant = None  # station 8 has one sensor: single occupancy

    while active_athletes:
        # Choose a random athlete to perform an action
        athlete = random.choice(active_athletes)
        stage = STAGES[athlete.stage_idx]
        now_ms = int(time.time() * 1000)

        # Check speed constraint (stagger actions slightly to allow concurrent processing)
        if time.time() - athlete.last_action_time < 0.05:
            time.sleep(0.01)
            continue

        athlete.last_action_time = time.time()

        if stage["type"] == "run":
            # Send run track mat crossing
            client.publish(
                RFID_TOPIC,
                json.dumps(
                    rfid_payload(
                        athlete,
                        location="running_track",
                        station_number=9,
                        timestamp_ms=now_ms,
                        rssi=-45.0,
                    )
                ),
            )
            athlete.lap_count += 1
            print(f"🏃 [{athlete.name}] crossing Running Mat ({athlete.lap_count}/4 laps for {stage['name']})")

            # 4 laps completed -> enter workout station
            if athlete.lap_count >= 4:
                athlete.lap_count = 0
                athlete.stage_idx += 1
                next_stage = STAGES[athlete.stage_idx]
                print(f"📍 [{athlete.name}] finished running. Moving to {next_stage['name']}")

        elif stage["type"] == "station":
            # Simulate walking to cardio machine (Mat crossing at Station)
            client.publish(
                RFID_TOPIC,
                json.dumps(
                    rfid_payload(
                        athlete,
                        location="start_line",
                        station_number=stage["station"],
                        timestamp_ms=now_ms,
                    )
                ),
            )
            print(f"🔌 [{athlete.name}] detected at Station {stage['station']} ({stage['name']})")

            # Wait a tiny bit, then complete it via HTTP force complete
            time.sleep(0.05)
            if not force_complete_stage(athlete.tag_id):
                print(f"⚠️ [{athlete.name}] could not complete {stage['name']}; will retry.")
                continue
            print(f"✅ [{athlete.name}] finished workout at {stage['name']}!")
            athlete.stage_idx += 1

        elif stage["type"] == "lengths":
            # Alternating crossings; the entry read at the start mat registers
            # the first position, so N lengths take N+1 crossings
            location = "start_line" if athlete.lap_count % 2 == 0 else "finish_line"
            client.publish(
                RFID_TOPIC,
                json.dumps(
                    rfid_payload(
                        athlete,
                        location=location,
                        station_number=stage["station"],
                        timestamp_ms=now_ms,
                    )
                ),
            )
            athlete.lap_count += 1

            if athlete.lap_count == 1:
                print(f"🏋️ [{athlete.name}] entered {stage['name']} lane")
            else:
                print(f"🏋️ [{athlete.name}] completed length {athlete.lap_count - 1}/{stage['lengths']} on {stage['name']}")

            # Lengths completed -> walks back to run track
            if athlete.lap_count >= stage["lengths"] + 1:
                athlete.lap_count = 0
                athlete.stage_idx += 1
                print(f"📍 [{athlete.name}] finished {stage['name']}. Heading to running track.")

        elif stage["type"] == "wallballs":
            # Station 8 sensor tracks one athlete at a time; wait until free
            if wallball_occupant not in (None, athlete.tag_id):
                time.sleep(0.01)
                continue
            wallball_occupant = athlete.tag_id

            # Always send RFID crossing mat read at Station 8 to bind tag_id in backend before sending reps
            client.publish(
                RFID_TOPIC,
                json.dumps(
                    rfid_payload(
                        athlete,
                        location="start_line",
                        station_number=stage["station"],
                        timestamp_ms=now_ms,
                    )
                ),
            )
            if athlete.lap_count == 0:
                print(f"🔌 [{athlete.name}] entered Wall Balls (Station {stage['station']})")
            time.sleep(0.01)

            # Send wallball reps (15 reps per tick to make it faster)
            for _ in range(15):
                client.publish(
                    WALLBALL_TOPIC,
                    json.dumps(wallball_payload(stage["station"], now_ms)),
                )
                time.sleep(0.005)

            athlete.lap_count += 15
            print(f"🏀 [{athlete.name}] threw Wall Balls ({athlete.lap_count}/75 reps)")

            if athlete.lap_count >= 75:
                athlete.lap_count = 0
                athlete.stage_idx += 1
                athlete.is_finished = True
                wallball_occupant = None
                print(f"🏁 [{athlete.name}] finished Wall Balls and crossed the FINISH LINE!")

        elif stage["type"] == "finished":
            athlete.is_finished = True

        # Remove finished athletes from simulator loop
        if athlete.is_finished:
            print(f"🏆 [{athlete.name}] HAS OFFICIALLY FINISHED THE HYROX RACE!")
            active_athletes.remove(athlete)

        time.sleep(0.02)

    print("\n🏁 Simulation completed! All athletes finished.")
    client.loop_stop()
    client.disconnect()

if __name__ == "__main__":
    main()
