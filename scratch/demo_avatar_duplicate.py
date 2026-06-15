import sys
import time
import json
import random
import urllib.request

# Tiny 1x1 valid base64 webp image
TINY_WEBP_BASE64 = (
    "data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4H"
)

ATHLETE_NAMES = [
    ("Tony", "RD", True),
    ("Tony", "Sales", False),
    ("Alex", "17FIT", True),
    ("Bob", "紅隊", False),
    ("Charlie", None, False),
    ("David", "藍隊", True),
    ("Eva", "RD", False),
    ("Frank", None, False),
    ("Grace", "Sales", True),
    ("Henry", "17FIT", False),
    ("Ivy", None, True),
    ("Jack", "紅隊", False),
    ("Karen", "藍隊", False),
    ("Leo", None, False),
    ("Mia", "RD", False),
    ("Nathan", "Sales", False),
    ("Olivia", None, False),
    ("Peter", "17FIT", False),
    ("Queen", "紅隊", False),
    ("Roy", "藍隊", False)
]

def post_json(url, data=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(data or {}).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req) as response:
            return response.read()
    except Exception as e:
        print(f"Error posting to {url}: {e}")
        return None

def main():
    base_url = "http://127.0.0.1:8000"
    num_devices = 20
    
    print("1. Resetting race...")
    post_json(f"{base_url}/api/race/reset")
    time.sleep(0.5)

    print("2. Discovering 20 active devices (bike-01 to bike-20)...")
    for i in range(1, num_devices + 1):
        node_id = f"bike-{i:02d}"
        equip_id = f"BIKE_{i:02d}"
        post_json(f"{base_url}/api/test/telemetry", {
            "node_id": node_id, "equipment_id": equip_id, "equipment_type": "fan_bike", "distance_m": 0.0, "timestamp_epoch_ms": int(time.time()*1000)
        })
    time.sleep(0.5)

    print("3. Assigning 20 devices to physical stations...")
    for i in range(1, num_devices + 1):
        node_id = f"bike-{i:02d}"
        post_json(f"{base_url}/api/stations/assign", {"station_number": i, "node_id": node_id})
    time.sleep(0.5)

    print("4. Registering 20 athletes (some with teams & avatars, including duplicate Tonys)...")
    for i in range(1, num_devices + 1):
        name, team, has_avatar = ATHLETE_NAMES[i-1]
        payload = {
            "station_number": i,
            "athlete_name": name,
            "team_name": team,
            "avatar_base64": TINY_WEBP_BASE64 if has_avatar else None
        }
        post_json(f"{base_url}/api/race/register", payload)
    time.sleep(0.5)

    # 5. Parse command line arguments for challenge configuration
    race_type = "distance"
    target_value = 500.0
    duration_sec = 0

    if len(sys.argv) > 1:
        arg_type = sys.argv[1].lower()
        if arg_type == "calories":
            race_type = "calories"
            target_value = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0
            duration_sec = 0
        elif arg_type == "time":
            race_type = "time"
            target_value = 0.0
            duration_sec = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        elif arg_type == "max_power":
            race_type = "max_power"
            target_value = 0.0
            duration_sec = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        elif arg_type == "distance":
            race_type = "distance"
            target_value = float(sys.argv[2]) if len(sys.argv) > 2 else 500.0
            duration_sec = 0

    print(f"5. Configuring challenge: {race_type} (target: {target_value}, duration: {duration_sec}s)...")
    post_json(f"{base_url}/api/race/configure", {
        "race_type": race_type, 
        "target_value": target_value, 
        "duration_sec": duration_sec
    })
    time.sleep(0.5)

    print("6. Starting the race (START)!")
    post_json(f"{base_url}/api/race/start")
    
    # 7. Simulate telemetry updates
    distances = [0.0] * num_devices
    calories_list = [0.0] * num_devices
    
    print(f"Streaming live telemetry updates for 20 competitors ({race_type} challenge)...")
    step = 0
    while True:
        step += 1
        print(f"  Step {step}:")
        
        # Check finish conditions
        if race_type == "distance":
            if all(d >= target_value for d in distances):
                break
        elif race_type == "calories":
            if all(c >= target_value for c in calories_list):
                break
        elif race_type in ("time", "max_power"):
            if step > duration_sec:
                break
                
        for i in range(num_devices):
            node_id = f"bike-{i+1:02d}"
            equip_id = f"BIKE_{i+1:02d}"
            
            # Determine if athlete is finished
            is_finished = False
            if race_type == "distance" and distances[i] >= target_value:
                is_finished = True
            elif race_type == "calories" and calories_list[i] >= target_value:
                is_finished = True
                
            if is_finished:
                speed = 0.0
                power = 0
            else:
                speed = random.uniform(22.0, 45.0)
                delta_dist = (speed / 3.6) * 1.0
                distances[i] += delta_dist
                power = int(speed * 6.5)
                calories_list[i] += (power * 1.0) / 1000.0
            
            payload = {
                "node_id": node_id, 
                "equipment_id": equip_id, 
                "equipment_type": "fan_bike",
                "distance_m": round(distances[i], 1), 
                "instantaneous_speed_kph": round(speed, 1), 
                "cadence_rpm": int(speed * 2.1) if not is_finished else 0,
                "power_watts": power,
                "calories": round(calories_list[i], 1),
                "elapsed_time_ms": step * 1000, 
                "timestamp_epoch_ms": int(time.time() * 1000)
            }
            
            post_json(f"{base_url}/api/test/telemetry", payload)
            
        time.sleep(1.0)

    print("8. Stopping the race (STOP)...")
    post_json(f"{base_url}/api/race/stop")
    print("20-device Simulation complete! Check your Live Leaderboard.")

if __name__ == "__main__":
    main()
