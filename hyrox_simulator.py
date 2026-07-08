"""Resource-aware Hyrox simulator (Phase 6a).

Loads a venue config, registers athletes in staggered batches, starts the race,
then drives each athlete asynchronously in their own thread.
Measures queueing/waiting times when resources are occupied, and prints
a comprehensive bottleneck summary analysis at the end.

Runs in an infinite loop of races until manually stopped.

Venue Configuration:
  - 8 Treadmills
  - 4 SkiErgs
  - 4 Rowers
  - 4 Turf Lanes
  - 4 Wall Ball devices

Time duration is scaled to 1/3 of the real-world standard duration.
Staggered waves: Next wave enters when all athletes of current wave complete run_1.
"""

import os
import json
import time
import random
import threading
import urllib.request
from paho.mqtt import client as mqtt_client

MQTT_BROKER = os.environ.get("FITRACE_MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("FITRACE_MQTT_PORT", "1883"))
BASE_URL = os.environ.get("FITRACE_HYROX_API", "http://localhost:8000/api/hyrox")
ADMIN_TOKEN = os.environ.get("FITRACE_ADMIN_TOKEN", "")

# 12 athletes split into 3 staggered waves of 4 athletes each
MOCK_ATHLETES = [
    # Batch 1
    {"name": "Alex Rivera", "tag": "TAG_ALEX", "run_speed": 13.5, "workout_rate": 1.05},
    {"name": "Sarah Cheng", "tag": "TAG_SARAH", "run_speed": 12.0, "workout_rate": 0.98},
    {"name": "Diego Mora", "tag": "TAG_DIEGO", "run_speed": 11.2, "workout_rate": 0.88},
    {"name": "Emma Watson", "tag": "TAG_EMMA", "run_speed": 14.5, "workout_rate": 1.15},
    
    # Batch 2 (Staggered Wave 2)
    {"name": "John Doe", "tag": "TAG_JOHN", "run_speed": 13.0, "workout_rate": 1.02},
    {"name": "Jane Smith", "tag": "TAG_JANE", "run_speed": 12.5, "workout_rate": 1.00},
    {"name": "Bob Johnson", "tag": "TAG_BOB", "run_speed": 11.8, "workout_rate": 0.92},
    {"name": "Alice Williams", "tag": "TAG_ALICE", "run_speed": 14.0, "workout_rate": 1.10},
    
    # Batch 3 (Staggered Wave 3)
    {"name": "Charlie Brown", "tag": "TAG_CHARLIE", "run_speed": 11.0, "workout_rate": 0.85},
    {"name": "Diana Prince", "tag": "TAG_DIANA", "run_speed": 15.0, "workout_rate": 1.20},
    {"name": "Evan Wright", "tag": "TAG_EVAN", "run_speed": 12.8, "workout_rate": 0.95},
    {"name": "Fiona Gallagher", "tag": "TAG_FIONA", "run_speed": 13.2, "workout_rate": 1.01},
]

# Course definition in sequence
STAGES = [
    ("run_1", "ftms"), ("ski_erg", "ftms"), ("run_2", "ftms"),
    ("sled_push", "lane"), ("run_3", "ftms"), ("sled_pull", "lane"),
    ("run_4", "ftms"), ("burpee_broad", "lane"), ("run_5", "ftms"),
    ("row", "ftms"), ("run_6", "ftms"), ("farmers_carry", "lane"),
    ("run_7", "ftms"), ("sandbag_lunges", "lane"), ("run_8", "ftms"),
    ("wall_balls", "reps"),
]

stage_name_to_idx = {name: idx for idx, (name, _) in enumerate(STAGES)}

# Shared simulation state (thread-safe protected by state_lock)
state_lock = threading.Lock()
occupied_resources = set()
current_backend_stages = {}
current_backend_statuses = {}
race_active = False
group_queues = {}
SIM_SPEED_MULTIPLIER = 4.5

# Resource group mappings
group_map = {
    "run_treadmills": ["treadmill-%d" % i for i in range(1, 9)],
    "ski_erg_pool": ["ski-%d" % i for i in range(1, 5)],
    "row_pool": ["row-%d" % i for i in range(1, 5)],
    "shared_turf_lanes": ["lane-%d" % i for i in range(1, 5)],
    "wall_ball_targets": ["wallball-%d" % i for i in range(1, 5)],
}


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


def api_get(path):
    headers = {}
    if ADMIN_TOKEN:
        headers["X-FitRace-Admin-Token"] = ADMIN_TOKEN
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        return None


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
         "units": [_ftms_unit("treadmill", i, "T") for i in range(1, 9)]},
        {"group_id": "ski_erg_pool", "resource_type": "ftms_machine_pool", "stage_candidates": [],
         "units": [_ftms_unit("ski", i, "S") for i in range(1, 5)]},
        {"group_id": "row_pool", "resource_type": "ftms_machine_pool", "stage_candidates": [],
         "units": [_ftms_unit("row", i, "R") for i in range(1, 5)]},
        {"group_id": "shared_turf_lanes", "resource_type": "rfid_lane_pool", "stage_candidates": [],
         "units": [{
             "resource_id": f"lane-{i}", "display_name": f"Lane {i}",
             "sensor_class": "rfid_endpoint_pair",
             "start_endpoint": {"node_id": f"rfid-lane-{i}", "antenna_id": f"L{i}_START"},
             "finish_endpoint": {"node_id": f"rfid-lane-{i}", "antenna_id": f"L{i}_FINISH"},
         } for i in range(1, 5)]},
        {"group_id": "wall_ball_targets", "resource_type": "rep_counter_pool", "stage_candidates": [],
         "units": [{
             "resource_id": f"wallball-{i}", "display_name": f"Wall Ball {i}",
             "sensor_class": "rep_counter", "node_id": f"edge-wb-{i}",
             "entry_gate": {"node_id": f"rfid-wb-{i}", "antenna_id": f"W{i}_GATE"},
         } for i in range(1, 5)]},
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

    def ftms(self, node, distance):
        self.client.publish(f"gym/telemetry/ftms/{node}", json.dumps({
            "node_id": node, "distance_m": distance, "timestamp_epoch_ms": _now_ms()}))

    def wallball(self, node):
        self.client.publish(f"gym/telemetry/wallball/{node}", json.dumps({
            "node_id": node, "timestamp_epoch_ms": _now_ms()}))


class AthleteSimState:
    def __init__(self, spec, wave):
        self.spec = spec
        self.wave = wave
        self.tag = spec["tag"]
        self.name = spec["name"]
        
        # Real-world baseline performance values (fresh state)
        self.base_run_speed_mps = spec["run_speed"] / 3.6
        self.base_ski_speed_mps = 3.7 * spec["workout_rate"]
        self.base_row_speed_mps = 3.85 * spec["workout_rate"]
        
        # Baseline duration per length for turf lane exercises (fresh state)
        self.base_lane_durations = {
            "sled_push": 37.5 / spec["workout_rate"],
            "sled_pull": 45.0 / spec["workout_rate"],
            "burpee_broad": 52.5 / spec["workout_rate"],
            "farmers_carry": 30.0 / spec["workout_rate"],
            "sandbag_lunges": 45.0 / spec["workout_rate"],
        }
        self.base_wallball_rep_duration = 3.2 / spec["workout_rate"]

        self.stage_idx = 0
        self.status = "waiting_to_start"  # waiting_to_start | binding | working | finished
        self.assigned_resource = None
        self.stage_progress = 0.0
        
        # Tracking stats
        self.start_time = None
        self.end_time = None
        self.stage_times = {name: 0.0 for name, _ in STAGES}
        self.queue_times = {name: 0.0 for name, _ in STAGES}

        # Lane-specific tracking
        self.lane_lengths_completed = 0
        self.lane_last_endpoint = None
        self.lane_next_crossing_due = 0.0

        # Reps-specific tracking
        self.reps_completed = 0
        self.rep_next_due = 0.0


def athlete_thread_worker(athlete_state, sim):
    # Register the athlete at the backend
    api_post("/register", {
        "athlete_name": athlete_state.name,
        "rfid_tag_id": athlete_state.tag,
        "division": "individual"
    })
    
    # Wait until the wave is released
    while True:
        with state_lock:
            if race_active and athlete_state.status == "binding":
                break
        time.sleep(0.2)

    athlete_state.start_time = time.time()

    for idx, (stage_name, kind) in enumerate(STAGES):
        athlete_state.stage_idx = idx
        
        # Decide which resource group is needed
        if stage_name.startswith("run_"):
            group_id = "run_treadmills"
        elif stage_name == "ski_erg":
            group_id = "ski_erg_pool"
        elif stage_name == "row":
            group_id = "row_pool"
        elif stage_name == "wall_balls":
            group_id = "wall_ball_targets"
        else:
            group_id = "shared_turf_lanes"

        # Enter FIFO queue
        with state_lock:
            q_list = group_queues.setdefault(group_id, [])
            if athlete_state.tag not in q_list:
                q_list.append(athlete_state.tag)

        # Report queue entry to backend for live God-View visual dashboard queue tracking
        api_post("/queue", {
            "subject_id": athlete_state.tag,
            "group_id": group_id,
            "wait_start_epoch_ms": _now_ms()
        })

        wait_start = time.time()
        res_id = None
        
        while True:
            with state_lock:
                q_list = group_queues.get(group_id, [])
                if q_list and q_list[0] == athlete_state.tag:
                    # We are at the front of the queue. Check for free units.
                    units = group_map.get(group_id, [])
                    for u in units:
                        if u not in occupied_resources:
                            res_id = u
                            occupied_resources.add(res_id)
                            q_list.pop(0)
                            break
            if res_id:
                break
            time.sleep(0.05)  # poll faster (50ms) for responsive handoffs

        wait_duration = time.time() - wait_start
        athlete_state.queue_times[stage_name] = wait_duration
        if wait_duration > 0.5:
            print(f"[QUEUE] {athlete_state.name} waited {wait_duration:.2f}s for {stage_name} ({group_id})")

        # Report queue leave to backend
        api_post("/queue", {
            "subject_id": athlete_state.tag,
            "group_id": group_id,
            "wait_start_epoch_ms": None
        })

        athlete_state.assigned_resource = res_id
        athlete_state.status = "working"
        athlete_state.stage_progress = 0.0

        res_num = res_id.split('-')[1]

        # Bind RFID + Send baseline 0.0 distance
        if group_id == "run_treadmills":
            sim.rfid(f"rfid-treadmill-{res_num}", f"T{res_num}_GATE", athlete_state.tag)
            sim.ftms(f"edge-{res_id}", 0.0)
        elif group_id == "ski_erg_pool":
            sim.rfid(f"rfid-ski-{res_num}", f"S{res_num}_GATE", athlete_state.tag)
            sim.ftms(f"edge-{res_id}", 0.0)
        elif group_id == "row_pool":
            sim.rfid(f"rfid-row-{res_num}", f"R{res_num}_GATE", athlete_state.tag)
            sim.ftms(f"edge-{res_id}", 0.0)
        elif group_id == "wall_ball_targets":
            sim.rfid(f"rfid-wb-{res_num}", f"W{res_num}_GATE", athlete_state.tag)

        # Set up stage triggers
        stage_start_time = time.time()
        
        # Calculate active stage metrics based on linear fatigue:
        # fatigue drops by 1.2% per stage completed
        fatigue = 1.0 - (idx * 0.012)
        
        if kind == "lane":
            athlete_state.lane_lengths_completed = 0
            athlete_state.lane_last_endpoint = "START"
            sim.rfid(f"rfid-{res_id}", f"L{res_num}_START", athlete_state.tag)
            
            base_duration = athlete_state.base_lane_durations.get(stage_name, 30.0)
            active_duration = (base_duration / fatigue) / SIM_SPEED_MULTIPLIER
            athlete_state.lane_next_crossing_due = stage_start_time + active_duration
        elif kind == "reps":
            athlete_state.reps_completed = 0
            active_duration = (athlete_state.base_wallball_rep_duration / fatigue) / SIM_SPEED_MULTIPLIER
            athlete_state.rep_next_due = stage_start_time + active_duration

        print(f"[BIND] {athlete_state.name} claimed {res_id} for {stage_name}")

        # Worker loop for stage progression
        while True:
            now = time.time()
            fatigue = 1.0 - (idx * 0.012)
            
            if kind == "ftms":
                if stage_name.startswith("run_"):
                    base_speed = athlete_state.base_run_speed_mps
                elif stage_name == "ski_erg":
                    base_speed = athlete_state.base_ski_speed_mps
                else:
                    base_speed = athlete_state.base_row_speed_mps
                
                speed_variation = random.uniform(0.95, 1.05)
                active_speed = (base_speed * fatigue) * SIM_SPEED_MULTIPLIER * speed_variation
                athlete_state.stage_progress += active_speed * 0.2
                sim.ftms(f"edge-{res_id}", athlete_state.stage_progress)
            elif kind == "lane":
                if now >= athlete_state.lane_next_crossing_due:
                    if athlete_state.lane_last_endpoint == "START":
                        sim.rfid(f"rfid-{res_id}", f"L{res_num}_FINISH", athlete_state.tag)
                        athlete_state.lane_last_endpoint = "FINISH"
                    else:
                        sim.rfid(f"rfid-{res_id}", f"L{res_num}_START", athlete_state.tag)
                        athlete_state.lane_last_endpoint = "START"
                    athlete_state.lane_lengths_completed += 1
                    
                    base_duration = athlete_state.base_lane_durations.get(stage_name, 30.0)
                    active_duration = (base_duration / fatigue) / SIM_SPEED_MULTIPLIER
                    athlete_state.lane_next_crossing_due = now + active_duration * random.uniform(0.9, 1.1)
            elif kind == "reps":
                if now >= athlete_state.rep_next_due:
                    athlete_state.reps_completed += 1
                    sim.wallball(f"edge-wb-{res_num}")
                    
                    active_duration = (athlete_state.base_wallball_rep_duration / fatigue) / SIM_SPEED_MULTIPLIER
                    athlete_state.rep_next_due = now + active_duration * random.uniform(0.9, 1.1)

            # Query backend state (cached in syncer) to check for completion
            with state_lock:
                current_backend_stage = current_backend_stages.get(athlete_state.tag)
                current_backend_status = current_backend_statuses.get(athlete_state.tag)

            if current_backend_status == "finished":
                break
            if current_backend_stage:
                backend_stage_idx = stage_name_to_idx.get(current_backend_stage, -1)
                if backend_stage_idx > idx:
                    break

            time.sleep(0.2)

        # Release resource and record stats
        stage_end_time = time.time()
        athlete_state.stage_times[stage_name] = stage_end_time - stage_start_time
        print(f"[COMPLETE] {athlete_state.name} finished {stage_name} ({res_id}) in {stage_end_time - stage_start_time:.2f}s")
        
        with state_lock:
            occupied_resources.remove(res_id)
            athlete_state.assigned_resource = None

    athlete_state.end_time = time.time()
    athlete_state.status = "finished"
    print(f"[FINISH] {athlete_state.name} completed the whole course in {athlete_state.end_time - athlete_state.start_time:.2f}s!")


def state_syncer_worker(states):
    global current_backend_stages, current_backend_statuses
    while not all(s.status == "finished" for s in states):
        state_data = api_get("/state")
        if state_data and "subjects" in state_data:
            with state_lock:
                for sub in state_data["subjects"]:
                    current_backend_stages[sub["subject_id"]] = sub["current_stage"]
                    current_backend_statuses[sub["subject_id"]] = sub["status"]
        time.sleep(0.5)


def format_real_time(sim_seconds):
    real_seconds = sim_seconds * SIM_SPEED_MULTIPLIER
    hours = int(real_seconds // 3600)
    minutes = int((real_seconds % 3600) // 60)
    seconds = int(real_seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def print_bottleneck_report(states):
    print("\n" + "="*80)
    print("               HYROX SIMULATOR FINISH & BOTTLENECK REPORT")
    print("               (SHOWING REAL-WORLD EQUIVALENT PACE TIMES)")
    print("="*80)
    
    # 0. Individual split times report
    print("\n" + "="*80)
    print("                       INDIVIDUAL ATHLETE SPLITS REPORT")
    print("="*80)
    for s in states:
        total_t = s.end_time - s.start_time if s.end_time else 0.0
        print(f"\n👤 Athlete: {s.name} (Wave {s.wave})")
        print(f"   Total Time: {format_real_time(total_t)} | Work: {format_real_time(sum(s.stage_times.values()))} | Queue: {format_real_time(sum(s.queue_times.values()))}")
        print(f"   {'Stage Name':<16} | {'Split Time (Real)':<18} | {'Queue Time (Real)':<18}")
        print("   " + "-"*56)
        for name, _ in STAGES:
            t_work = s.stage_times.get(name, 0.0)
            t_queue = s.queue_times.get(name, 0.0)
            print(f"   {name:<16} | {format_real_time(t_work):<18} | {format_real_time(t_queue):<18}")
    print("="*80 + "\n")

    # 1. Athlete results summary table
    print(f"{'Athlete Name':<18} | {'Wave':<4} | {'Total Time':<12} | {'Work Time':<12} | {'Queue Time':<12} | {'Status':<8}")
    print("-"*80)
    for s in states:
        total_time = s.end_time - s.start_time if s.end_time else 0.0
        work_time = sum(s.stage_times.values())
        queue_time = sum(s.queue_times.values())
        print(f"{s.name:<18} | {s.wave:<4} | {format_real_time(total_time):<12} | {format_real_time(work_time):<12} | {format_real_time(queue_time):<12} | {s.status:<8}")
    print("-"*80)

    # 2. Stage/Resource bottlenecks table
    print("\n" + "="*80)
    print("                       STAGE QUEUEING BOTTLENECK SUMMARY")
    print("="*80)
    print(f"{'Stage Name':<16} | {'Resource Group':<20} | {'Total Queue Time':<18} | {'Avg Queue / Athlete'}")
    print("-"*80)
    
    stage_groups = {
        "run_1": "run_treadmills", "ski_erg": "ski_erg_pool", "run_2": "run_treadmills",
        "sled_push": "shared_turf_lanes", "run_3": "run_treadmills", "sled_pull": "shared_turf_lanes",
        "run_4": "run_treadmills", "burpee_broad": "shared_turf_lanes", "run_5": "run_treadmills",
        "row": "row_pool", "run_6": "run_treadmills", "farmers_carry": "shared_turf_lanes",
        "run_7": "run_treadmills", "sandbag_lunges": "shared_turf_lanes", "run_8": "run_treadmills",
        "wall_balls": "wall_ball_targets"
    }

    for name, _ in STAGES:
        grp = stage_groups.get(name, "unknown")
        total_q = sum(s.queue_times.get(name, 0.0) for s in states)
        avg_q = total_q / len(states)
        print(f"{name:<16} | {grp:<20} | {format_real_time(total_q):<18} | {format_real_time(avg_q)}")
    print("-"*80)
    
    # 3. Overall recommendations
    print("\n[RECOMMENDATION]")
    groups_total_queue = {}
    for name, grp in stage_groups.items():
        groups_total_queue[grp] = groups_total_queue.get(grp, 0.0) + sum(s.queue_times.get(name, 0.0) for s in states)
    
    sorted_groups = sorted(groups_total_queue.items(), key=lambda x: x[1], reverse=True)
    top_bottleneck, bottleneck_time = sorted_groups[0]
    
    if bottleneck_time > 0.2:
        print(f"⚠️  BOTTLENECK DETECTED at [{top_bottleneck}] with total queue delay of {format_real_time(bottleneck_time)} across all waves.")
        print(f"💡 Suggestion: Consider increasing units in [{top_bottleneck}] to improve wave throughput.")
    else:
        print("✅ No major bottlenecks detected. Venue capacity is well-proportioned for this stagger rate.")
    print("="*80 + "\n")


def main():
    global race_active
    print("Connecting to MQTT...")
    client = mqtt_client.Client(callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
    try:
        client.connect(MQTT_BROKER, MQTT_PORT)
        client.loop_start()
    except Exception as e:
        print("MQTT connection failed:", e)
        return

    # Post venue configuration to the Hub
    venue_payload = build_venue()
    sim = Sim(client)

    # Run in an infinite loop until manually stopped (Ctrl+C)
    race_counter = 1
    while True:
        print(f"\n=======================================================")
        print(f"              STARTING SIMULATED RACE #{race_counter}")
        print(f"=======================================================")

        if not api_post("/venue-config", {"venue": venue_payload, "mode": "training"}):
            print("Could not load venue config; is Hub running?")
            time.sleep(5)
            continue

        print("Venue loaded. Initializing athletes...")

        # Instantiate athlete states in 3 waves of 4 athletes each
        states = []
        for idx, a in enumerate(MOCK_ATHLETES):
            wave = (idx // 4) + 1
            states.append(AthleteSimState(a, wave))

        # Clear occupied resources and backend stage caches
        with state_lock:
            occupied_resources.clear()
            current_backend_stages.clear()
            current_backend_statuses.clear()
            group_queues.clear()
            race_active = False

        # Spawn worker threads for all athletes
        threads = []
        for s in states:
            t = threading.Thread(target=athlete_thread_worker, args=(s, sim), daemon=True)
            threads.append(t)
            t.start()

        # Start background API state syncer
        syncer = threading.Thread(target=state_syncer_worker, args=(states,), daemon=True)
        syncer.start()

        # Trigger Hub start
        api_post("/start")
        with state_lock:
            race_active = True
        print("Race started.\n")

        # Release WAVE 1
        for s in states:
            if s.wave == 1:
                with state_lock:
                    s.status = "binding"
        print(">>> released WAVE 1 athletes.")

        current_wave = 1
        total_waves = 3

        while not all(s.status == "finished" for s in states):
            time.sleep(1.0)
            
            # Check staggered release condition for the next wave
            if current_wave < total_waves:
                # Check if all athletes in the CURRENT wave have completed run_1 (advanced past index 0)
                wave_athletes = [s for s in states if s.wave == current_wave]
                if all(s.stage_idx > 0 for s in wave_athletes):
                    current_wave += 1
                    # Release next wave
                    for s in states:
                        if s.wave == current_wave:
                            with state_lock:
                                s.status = "binding"
                    print(f"\n>>> released WAVE {current_wave} athletes (Wave {current_wave-1} all finished run_1).")

        # Wave completed! Print report
        print_bottleneck_report(states)
        
        race_counter += 1
        print("\nRace completed! Resting for 10 seconds before automatically resetting and starting the next race...\n")
        time.sleep(10.0)


if __name__ == "__main__":
    main()
