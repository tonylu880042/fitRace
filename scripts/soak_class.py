#!/usr/bin/env python3
"""Class-mode soak test: N simulated devices publishing telemetry over MQTT.

Drives a live Hub the same way real Edge Nodes do (MQTT status heartbeats +
1 Hz telemetry), starts a class, watches the dashboard WebSocket and the Pi's
CPU/RSS for the whole run, then restores whatever it changed.

Usage:
    .venv/bin/python scripts/soak_class.py --hub 192.168.0.130 --devices 20 --minutes 30

ponytail: one file, no framework. Sampling is 60 s wall-clock, good enough to
spot a leak or a stall; add finer sampling only if a run looks suspicious.
"""

import argparse
import asyncio
import contextlib
import json
import math
import os
import subprocess
import time
from pathlib import Path

import httpx
import websockets
from paho.mqtt import client as mqtt

STREAMS_PER_EDGE = 4  # mirrors a real node's FTMS channel budget


def build_devices(count):
    devices = []
    for i in range(count):
        edge = f"fitrace-sim-{i // STREAMS_PER_EDGE + 1:02d}"
        devices.append(
            {
                "edge_node_id": edge,
                "node_id": f"{edge}-{i % STREAMS_PER_EDGE + 1:02d}",
                "equipment_id": f"SIM_{i + 1:02d}",
                "equipment_type": "spin_bike",
                "distance_m": 0.0,
                "calories": 0.0,
            }
        )
    return devices


def class_plan(minutes):
    """warmup / 4x(work,rest) / cooldown, scaled to `minutes`."""
    total = int(minutes * 60)
    if total < 300:  # smoke-size run: no room for the full structure
        warm = max(10, total // 5)
        return {
            "segments": [
                {"kind": "warmup", "duration_sec": warm, "target_watts": 100},
                {
                    "kind": "work",
                    "duration_sec": max(10, total - warm),
                    "target_watts": 250,
                },
            ]
        }
    warm = cool = max(60, int(total * 0.15))
    block = (total - warm - cool) // 4
    rest = max(15, block // 5)
    work = block - rest
    segments = [{"kind": "warmup", "duration_sec": warm, "target_watts": 100}]
    for _ in range(4):
        segments.append({"kind": "work", "duration_sec": work, "target_watts": 250})
        segments.append({"kind": "rest", "duration_sec": rest})
    segments.append({"kind": "cooldown", "duration_sec": cool, "target_watts": 60})
    return {"segments": segments}


def host_sample(host, user):
    """loadavg + hub RSS/CPU + SoC temp, over ssh or locally when host is the
    machine we run on. Returns {} when the metrics are unreachable."""
    cmd = (
        "cat /proc/loadavg; "
        "ps -eo rss=,pcpu=,args= | grep -m1 '[h]ub_server'; "
        "awk '{print \"ticks=\" $14+$15}' /proc/$(pgrep -f '[h]ub_server' | head -1)/stat; "
        "vcgencmd measure_temp 2>/dev/null"
    )
    local = host in ("127.0.0.1", "localhost")
    argv = (
        ["bash", "-c", cmd]
        if local
        else [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            f"{user}@{host}",
            cmd,
        ]
    )
    try:
        out = subprocess.run(
            argv, capture_output=True, text=True, timeout=20
        ).stdout.splitlines()
    except Exception:
        return {}
    sample = {}
    if out:
        sample["load1"] = out[0].split()[0]
    for line in out[1:]:
        if line.startswith("ticks="):
            sample["hub_cpu_ticks"] = int(line.split("=")[1])
        elif line.startswith("temp="):
            sample["temp_c"] = line.split("=")[1].rstrip("'C\n")
        elif line.strip():
            parts = line.split()
            sample["hub_rss_mb"] = round(int(parts[0]) / 1024, 1)
            sample["hub_cpu_pct"] = parts[1]
    return sample


def self_sample():
    """This generator's own footprint, so the hub's numbers can be read net of
    the load we add by running on the same box."""
    try:
        rss_pages = int(Path("/proc/self/statm").read_text().split()[1])
        t = os.times()
        return {
            "gen_rss_mb": round(rss_pages * 4096 / 1024 / 1024, 1),
            "gen_cpu_s": round(
                t.user + t.system + t.children_user + t.children_system, 1
            ),
        }
    except Exception:
        return {}


class Soak:
    def __init__(self, args):
        self.args = args
        self.base = f"http://{args.hub}:8000"
        self.devices = build_devices(args.devices)
        self.http = httpx.AsyncClient(timeout=10.0)
        self.published = 0
        self.ws_messages = 0
        self.ws_last_ms = None
        self.ws_max_gap_ms = 0
        self.ws_reconnects = 0
        self.samples = []
        self.errors = []
        self.snapshot = {}

    # --- MQTT -------------------------------------------------------
    def mqtt_connect(self):
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.mq.connect(self.args.hub, 1883, keepalive=30)
        self.mq.loop_start()

    def publish_status(self):
        now = int(time.time() * 1000)
        by_edge = {}
        for d in self.devices:
            by_edge.setdefault(d["edge_node_id"], []).append(d)
        for edge, group in by_edge.items():
            payload = {
                "edge_node_id": edge,
                "hostname": edge,
                "ip": f"192.168.0.2{int(edge[-2:]):02d}",
                "status": "online",
                "software_version": "soak",
                "antenna_protocol_version": "uart-ftms-json-v1.1",
                "max_ftms_connections": 6,
                "available_channels": 2,
                "last_seen_epoch_ms": now,
                "equipment_streams": [
                    {
                        "node_id": d["node_id"],
                        "equipment_id": d["equipment_id"],
                        "equipment_type": d["equipment_type"],
                        "status": "online",
                        "antenna_channel": "uart-1",
                        "rssi": -70,
                    }
                    for d in group
                ],
            }
            self.mq.publish(f"fitrace/nodes/{edge}/status", json.dumps(payload), qos=1)

    def publish_telemetry(self, tick, elapsed_ms):
        now = int(time.time() * 1000)
        for i, d in enumerate(self.devices):
            # Each device sits on its own phase of the same effort wave, so
            # the leaderboard reorders continuously instead of freezing.
            watts = 150 + 90 * math.sin((tick + i * 3) / 25.0)
            speed = 22 + 8 * math.sin((tick + i * 5) / 30.0)
            d["distance_m"] += speed / 3.6
            d["calories"] += watts * 0.000239 * 1.0
            payload = {
                "node_id": d["node_id"],
                "edge_node_id": d["edge_node_id"],
                "equipment_id": d["equipment_id"],
                "equipment_type": d["equipment_type"],
                "instantaneous_speed_kph": round(speed, 2),
                "cadence_rpm": int(70 + 20 * math.sin((tick + i) / 20.0)),
                "power_watts": int(watts),
                "heart_rate_bpm": int(130 + 20 * math.sin((tick + i * 7) / 40.0)),
                "distance_m": round(d["distance_m"], 2),
                "elapsed_time_ms": elapsed_ms,
                "calories": round(d["calories"], 2),
                "timestamp_epoch_ms": now,
            }
            info = self.mq.publish(
                f"gym/telemetry/{d['node_id']}", json.dumps(payload), qos=1
            )
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                self.published += 1
            else:
                self.errors.append(f"mqtt publish rc={info.rc} {d['node_id']}")

    # --- Hub API ----------------------------------------------------
    async def api(self, method, path, **kw):
        r = await self.http.request(method, self.base + path, **kw)
        r.raise_for_status()
        return r.json() if r.content else {}

    async def setup(self):
        self.snapshot["state"] = await self.api("GET", "/api/race/state")
        self.snapshot["stations"] = await self.api("GET", "/api/stations")
        if self.snapshot["state"]["state"] == "RUNNING":
            raise SystemExit(
                "hub already has a RUNNING session -- refusing to disturb it"
            )

        self.mqtt_connect()
        self.publish_status()
        await asyncio.sleep(2)
        nodes = await self.api("GET", "/api/nodes")
        known = {
            s["node_id"] for n in nodes["nodes"] for s in n.get("equipment_streams", [])
        }
        missing = [d["node_id"] for d in self.devices if d["node_id"] not in known]
        if missing:
            raise SystemExit(f"hub did not register simulated nodes: {missing[:3]}...")

        for i, d in enumerate(self.devices):
            await self.api(
                "POST",
                "/api/stations/assign",
                json={
                    "station_number": self.args.station_base + i,
                    "node_id": d["node_id"],
                },
            )
        await self.api(
            "POST", "/api/class/configure", json=class_plan(self.args.minutes)
        )
        await self.api("POST", "/api/race/start")

    async def teardown(self):
        with contextlib.suppress(Exception):
            await self.api("POST", "/api/race/stop")
        with contextlib.suppress(Exception):
            self.snapshot["history"] = await self.api(
                "GET", "/api/class/history?limit=3"
            )
        with contextlib.suppress(Exception):
            await self.api("POST", "/api/race/reset")
        for i in range(len(self.devices)):
            with contextlib.suppress(Exception):
                await self.api(
                    "POST",
                    "/api/stations/assign",
                    json={"station_number": self.args.station_base + i, "node_id": ""},
                )
        original = self.snapshot["state"].get("class_plan")
        if original:
            with contextlib.suppress(Exception):
                await self.api("POST", "/api/class/configure", json=original)
        with contextlib.suppress(Exception):
            self.mq.loop_stop()
            self.mq.disconnect()
        await self.http.aclose()

    # --- Tasks ------------------------------------------------------
    async def telemetry_task(self, stop_at):
        tick = 0
        started = time.time()
        while time.time() < stop_at:
            self.publish_telemetry(tick, int((time.time() - started) * 1000))
            if tick % 5 == 0:
                self.publish_status()
            tick += 1
            await asyncio.sleep(1.0)

    async def ws_task(self, stop_at):
        url = f"ws://{self.args.hub}:8000/ws/dashboard"
        while time.time() < stop_at:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    while time.time() < stop_at:
                        await asyncio.wait_for(ws.recv(), timeout=30)
                        now = time.time() * 1000
                        if self.ws_last_ms:
                            self.ws_max_gap_ms = max(
                                self.ws_max_gap_ms, int(now - self.ws_last_ms)
                            )
                        self.ws_last_ms = now
                        self.ws_messages += 1
            except Exception as e:
                if time.time() < stop_at:
                    self.ws_reconnects += 1
                    self.errors.append(f"ws: {type(e).__name__}: {e}")
                    await asyncio.sleep(2)

    async def sample_task(self, stop_at, started):
        while time.time() < stop_at:
            await asyncio.sleep(self.args.sample_sec)
            t0 = time.time()
            try:
                state = await self.api("GET", "/api/race/state")
                latency_ms = int((time.time() - t0) * 1000)
            except Exception as e:
                self.errors.append(f"state api: {e}")
                continue
            seg = state.get("class_segment") or {}
            sample = {
                "elapsed_min": round((time.time() - started) / 60, 1),
                "state": state.get("state"),
                "session_mode": state.get("session_mode"),
                "segment": seg.get("index"),
                "kind": seg.get("kind"),
                "seg_remaining_s": (seg.get("segment_remaining_ms") or 0) // 1000,
                "leaderboard": len(state.get("leaderboard") or {}),
                "state_api_ms": latency_ms,
                "ws_msgs": self.ws_messages,
                "published": self.published,
                **host_sample(self.args.hub, self.args.ssh_user),
                **self_sample(),
            }
            if self.samples:
                prev = self.samples[-1]
                dt = (sample["elapsed_min"] - prev["elapsed_min"]) * 60
                if dt > 0 and "hub_cpu_ticks" in sample and "hub_cpu_ticks" in prev:
                    ticks = sample["hub_cpu_ticks"] - prev["hub_cpu_ticks"]
                    sample["hub_cpu_pct_now"] = round(ticks / 100.0 / dt * 100, 1)
                sample["ws_msgs_per_s"] = round(
                    (sample["ws_msgs"] - prev["ws_msgs"]) / dt, 1
                )
            self.samples.append(sample)
            print(json.dumps(sample, ensure_ascii=False), flush=True)

    async def run(self):
        await self.setup()
        started = time.time()
        stop_at = started + self.args.minutes * 60
        print(f"class started, running {self.args.minutes} min", flush=True)
        try:
            await asyncio.gather(
                self.telemetry_task(stop_at),
                self.ws_task(stop_at),
                self.sample_task(stop_at, started),
            )
        finally:
            final_state = None
            with contextlib.suppress(Exception):
                final_state = await self.api("GET", "/api/race/state")
            await self.teardown()
            self.report(started, final_state)

    def report(self, started, final_state):
        out = {
            "hub": self.args.hub,
            "devices": self.args.devices,
            "minutes": round((time.time() - started) / 60, 1),
            "telemetry_published": self.published,
            "ws_messages": self.ws_messages,
            "ws_max_gap_ms": self.ws_max_gap_ms,
            "ws_reconnects": self.ws_reconnects,
            "final_segment": (final_state or {}).get("class_segment"),
            "final_leaderboard_size": len((final_state or {}).get("leaderboard") or {}),
            "class_history": self.snapshot.get("history"),
            "errors": self.errors[:20],
            "error_count": len(self.errors),
            "samples": self.samples,
        }
        Path(self.args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"\nreport written: {self.args.out}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hub", default="192.168.0.130")
    p.add_argument("--devices", type=int, default=20)
    p.add_argument("--minutes", type=float, default=30)
    p.add_argument("--station-base", type=int, default=11)
    p.add_argument("--sample-sec", type=int, default=60)
    p.add_argument("--ssh-user", default="tony")
    p.add_argument("--out", default="soak_report.json")
    asyncio.run(Soak(p.parse_args()).run())


if __name__ == "__main__":
    main()
