#!/usr/bin/env python3
"""LiDAR TAS web server v2: mode control + motion-only tracking + TAS control."""

from __future__ import annotations

import argparse
import os
import socket
import statistics
import subprocess
import threading
import time
from collections import Counter, deque

import numpy as np
import ouster.sdk.core as core
import requests
from flask import Flask, jsonify, render_template_string, request as flask_request
from flask_cors import CORS

DEFAULT_LIDAR_HOST = "192.168.6.11"
DEFAULT_LIDAR_PORT = 7502
DEFAULT_KETI_TSN_DIR = "/home/kim/keti-tsn-cli-new"

# Common Ouster modes for OS-1 class sensors.
SUPPORTED_LIDAR_MODES = [
    "512x10",
    "512x20",
    "1024x10",
    "1024x20",
    "2048x10",
]

app = Flask(__name__)
CORS(app)

lock = threading.Lock()
running = True
lidar_connected = False
force_reconnect = False

latest_points = None
latest_motion_points = None
latest_tracks = []
latest_frame_id = 0

tas_state = {
    "enabled": False,
    "cycle_us": 1000,
    "open_us": 1000,
    "close_us": 0,
    "open_pct": 100,
    "mode": "single",
    "entries": [{"gate": 255, "duration_us": 1000}],
}

current_stats = {
    "fps": 0.0,
    "frame_completeness": 1.0,
    "valid_cols": 0,
    "total_cols": 2048,
    "points_per_frame": 0,
    "pkts_per_frame": 0,
    "pps": 0.0,
    "gap_mean_us": 0.0,
    "gap_stdev_us": 0.0,
    "gap_max_us": 0.0,
    "burst_pct": 0.0,
    "motion_points": 0,
    "motion_ratio": 0.0,
    "moving_objects": 0,
}

smoothed_stats = dict(current_stats)
EMA_ALPHA = 0.15

lidar_state = {
    "host": DEFAULT_LIDAR_HOST,
    "mode": "unknown",
    "udp_profile_lidar": "unknown",
    "columns_per_packet": 16,
    "timestamp_mode": "unknown",
    "sensor_reinit_in_progress": False,
}

motion_cfg = {
    "enabled": True,
    "show_motion_only": False,
    "voxel_m": 0.25,
    "bg_required_frames": 20,
    "bg_ready": False,
}

_bg_history = deque(maxlen=20)
_bg_voxel_set = set()


def api_post(host: str, path: str, timeout: float = 3.0) -> dict:
    r = requests.post(f"http://{host}{path}", timeout=timeout)
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return {"text": r.text, "status_code": r.status_code}


def api_get(host: str, path: str, timeout: float = 3.0) -> dict:
    return requests.get(f"http://{host}{path}", timeout=timeout).json()


def fetch_lidar_config(host: str) -> dict:
    cfg = api_get(host, "/api/v1/sensor/config")
    lidar_state["mode"] = cfg.get("lidar_mode", "unknown")
    lidar_state["udp_profile_lidar"] = cfg.get("udp_profile_lidar", "unknown")
    lidar_state["columns_per_packet"] = int(cfg.get("columns_per_packet", 16))
    lidar_state["timestamp_mode"] = cfg.get("timestamp_mode", "unknown")
    return cfg


def set_lidar_mode(host: str, mode: str) -> None:
    if mode not in SUPPORTED_LIDAR_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    lidar_state["sensor_reinit_in_progress"] = True
    try:
        api_post(host, f"/api/v1/sensor/cmd/set_config_param?args=lidar_mode%20{mode}")
        # Reinitialize applies changed config on most Ouster firmware versions.
        api_post(host, "/api/v1/sensor/cmd/reinitialize")
        time.sleep(2.0)
        fetch_lidar_config(host)
    finally:
        lidar_state["sensor_reinit_in_progress"] = False


def _patch_tas_yaml(keti_tsn_dir: str, content: str) -> bool:
    yaml_path = os.path.join(keti_tsn_dir, "lidar-tas", "_live_config.yaml")
    with open(yaml_path, "w", encoding="ascii") as f:
        f.write(content)
    result = subprocess.run(
        ["./keti-tsn", "patch", yaml_path],
        cwd=keti_tsn_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return "Failed" not in result.stdout and result.returncode == 0


def _normalize_entries(cycle_us: int, entries: list[dict]) -> list[dict]:
    if cycle_us <= 0:
        raise ValueError("cycle_us must be > 0")
    if not entries:
        raise ValueError("entries must not be empty")

    normalized = []
    total = 0
    for e in entries:
        gate = int(e.get("gate", 255))
        dur = int(e.get("duration_us", 0))
        if gate < 0 or gate > 255:
            raise ValueError("gate must be 0..255")
        if dur < 0:
            raise ValueError("duration_us must be >= 0")
        if dur == 0:
            continue
        normalized.append({"gate": gate, "duration_us": dur})
        total += dur

    if not normalized:
        raise ValueError("all entry durations are 0")
    if total != cycle_us:
        raise ValueError(f"sum(duration_us)={total} must equal cycle_us={cycle_us}")
    return normalized


def apply_tas_entries(keti_tsn_dir: str, cycle_us: int, entries: list[dict]) -> bool:
    normalized = _normalize_entries(cycle_us, entries)
    lines = [
        "- ? \"/ietf-interfaces:interfaces/interface[name='1']/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-sched-bridge:gate-parameter-table\"",
        "  : gate-enabled: true",
        "    admin-gate-states: 255",
        "    admin-cycle-time:",
        f"      numerator: {cycle_us * 1000}",
        "      denominator: 1000000000",
        "    admin-base-time:",
        "      seconds: 0",
        "      nanoseconds: 0",
        "    admin-control-list:",
        "      gate-control-entry:",
    ]
    for i, e in enumerate(normalized):
        lines.extend(
            [
                f"        - index: {i}",
                "          operation-name: set-gate-states",
                f"          gate-states-value: {e['gate']}",
                f"          time-interval-value: {e['duration_us'] * 1000}",
            ]
        )
    lines.append("    config-change: true")
    ok = _patch_tas_yaml(keti_tsn_dir, "\n".join(lines) + "\n")
    if ok:
        open_us = sum(e["duration_us"] for e in normalized if e["gate"] == 255)
        close_us = max(0, cycle_us - open_us)
        tas_state.update(
            {
                "enabled": close_us > 0,
                "cycle_us": cycle_us,
                "open_us": open_us,
                "close_us": close_us,
                "open_pct": round(open_us / cycle_us * 100),
                "entries": normalized,
                "mode": "multi" if len(normalized) > 2 else "single",
            }
        )
    return ok


def apply_tas(keti_tsn_dir: str, cycle_us: int, open_us: int) -> bool:
    open_us = max(0, min(int(open_us), int(cycle_us)))
    close_us = int(cycle_us) - open_us
    if close_us <= 0:
        entries = [{"gate": 255, "duration_us": int(cycle_us)}]
    else:
        entries = [
            {"gate": 255, "duration_us": open_us},
            {"gate": 254, "duration_us": close_us},
        ]
    return apply_tas_entries(keti_tsn_dir, int(cycle_us), entries)


def voxelize(points: np.ndarray, voxel_m: float) -> np.ndarray:
    if points.size == 0:
        return np.empty((0, 3), dtype=np.int32)
    return np.floor(points / voxel_m).astype(np.int32)


def detect_motion(points: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    global _bg_voxel_set

    if points.size == 0:
        return np.empty((0, 3), dtype=np.float32), []

    vox = voxelize(points, motion_cfg["voxel_m"])
    current_vox_set = set(map(tuple, vox.tolist()))

    if not motion_cfg["bg_ready"]:
        _bg_history.append(current_vox_set)
        if len(_bg_history) >= motion_cfg["bg_required_frames"]:
            count = Counter()
            for s in _bg_history:
                count.update(s)
            min_hits = max(2, int(len(_bg_history) * 0.7))
            _bg_voxel_set = {k for k, v in count.items() if v >= min_hits}
            motion_cfg["bg_ready"] = True
        return np.empty((0, 3), dtype=np.float32), []

    moving_vox = current_vox_set - _bg_voxel_set
    if not moving_vox:
        return np.empty((0, 3), dtype=np.float32), []

    # Map voxel -> points in that voxel.
    voxel_points = {}
    for i, vk in enumerate(map(tuple, vox.tolist())):
        if vk in moving_vox:
            voxel_points.setdefault(vk, []).append(points[i])

    # 6-neighbor clustering in voxel space.
    unvisited = set(moving_vox)
    clusters = []
    neighbors = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]

    while unvisited:
        seed = unvisited.pop()
        q = [seed]
        cluster_vox = {seed}
        while q:
            cx, cy, cz = q.pop()
            for dx, dy, dz in neighbors:
                nb = (cx + dx, cy + dy, cz + dz)
                if nb in unvisited:
                    unvisited.remove(nb)
                    q.append(nb)
                    cluster_vox.add(nb)
        clusters.append(cluster_vox)

    tracks = []
    moving_pts = []
    for cid, cvox in enumerate(sorted(clusters, key=len, reverse=True)[:5], start=1):
        pts = []
        for vk in cvox:
            pts.extend(voxel_points.get(vk, []))
        if len(pts) < 12:
            continue
        arr = np.asarray(pts, dtype=np.float32)
        center = arr.mean(axis=0)
        tracks.append(
            {
                "id": cid,
                "points": int(arr.shape[0]),
                "centroid": [float(center[0]), float(center[1]), float(center[2])],
            }
        )
        moving_pts.append(arr)

    if moving_pts:
        return np.concatenate(moving_pts, axis=0), tracks
    return np.empty((0, 3), dtype=np.float32), []


def reset_motion_background() -> None:
    global _bg_voxel_set
    _bg_history.clear()
    _bg_voxel_set = set()
    motion_cfg["bg_ready"] = False


def lidar_thread(host: str, port: int) -> None:
    global latest_points, latest_motion_points, latest_tracks, latest_frame_id
    global running, lidar_connected, force_reconnect, current_stats

    while running:
        lidar_connected = False
        try:
            meta_raw = requests.get(f"http://{host}/api/v1/sensor/metadata", timeout=5).text
            info = core.SensorInfo(meta_raw)
            fetch_lidar_config(host)

            pf = core.PacketFormat.from_info(info)
            batcher = core.ScanBatcher(info)
            xyzlut = core.XYZLut(info)

            w = info.format.columns_per_frame
            h = info.format.pixels_per_column
            pkt_size = pf.lidar_packet_size

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
            sock.bind(("0.0.0.0", port))
            sock.settimeout(1.0)
            lidar_connected = True

            scan = core.LidarScan(h, w, info.format.udp_profile_lidar)
            frame_times = deque(maxlen=20)
            last_time = time.time()
            pkt_timestamps = []

            while running and not force_reconnect:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    continue

                if len(data) != pkt_size:
                    continue

                pkt_timestamps.append(time.perf_counter())

                pkt_obj = core.LidarPacket(pkt_size)
                pkt_obj.buf[:] = np.frombuffer(data, dtype=np.uint8)
                done = batcher(pkt_obj, scan)

                if not done:
                    continue

                xyz = xyzlut(scan)
                status = scan.status
                valid_cols = int(np.count_nonzero(status))

                xyz_flat = xyz.reshape(-1, 3)
                d = np.linalg.norm(xyz_flat, axis=1)
                valid = (d > 0.3) & (d < 100)
                xyz_valid = xyz_flat[valid]

                moving_pts = np.empty((0, 3), dtype=np.float32)
                tracks = []
                if motion_cfg["enabled"]:
                    moving_pts, tracks = detect_motion(xyz_valid)

                now = time.time()
                frame_times.append(now - last_time)
                last_time = now
                fps = 1.0 / (sum(frame_times) / len(frame_times)) if frame_times else 0.0

                gap_mean = 0.0
                gap_stdev = 0.0
                gap_max = 0.0
                burst_pct = 0.0
                pps = 0.0
                if len(pkt_timestamps) > 2:
                    gaps = [(pkt_timestamps[i + 1] - pkt_timestamps[i]) * 1e6 for i in range(len(pkt_timestamps) - 1)]
                    gap_mean = statistics.mean(gaps)
                    gap_stdev = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
                    gap_max = max(gaps)
                    burst_pct = sum(1 for g in gaps if g < 50.0) / len(gaps) * 100.0
                    elapsed = pkt_timestamps[-1] - pkt_timestamps[0]
                    pps = len(pkt_timestamps) / elapsed if elapsed > 0 else 0.0

                completeness = valid_cols / w if w else 0.0
                motion_points = int(moving_pts.shape[0])
                motion_ratio = motion_points / max(1, int(xyz_valid.shape[0]))

                raw = {
                    "fps": fps,
                    "frame_completeness": completeness,
                    "valid_cols": valid_cols,
                    "total_cols": w,
                    "points_per_frame": int(xyz_valid.shape[0]),
                    "pkts_per_frame": len(pkt_timestamps),
                    "pps": pps,
                    "gap_mean_us": gap_mean,
                    "gap_stdev_us": gap_stdev,
                    "gap_max_us": gap_max,
                    "burst_pct": burst_pct,
                    "motion_points": motion_points,
                    "motion_ratio": motion_ratio,
                    "moving_objects": len(tracks),
                }
                current_stats = raw
                for k, v in raw.items():
                    smoothed_stats[k] = EMA_ALPHA * v + (1.0 - EMA_ALPHA) * smoothed_stats.get(k, v)

                with lock:
                    latest_points = xyz_valid.tolist()
                    latest_motion_points = moving_pts.tolist() if moving_pts.size else []
                    latest_tracks = tracks
                    latest_frame_id += 1

                pkt_timestamps = []
                scan = core.LidarScan(h, w, info.format.udp_profile_lidar)

            force_reconnect = False
            sock.close()

        except Exception as e:
            lidar_connected = False
            print(f"LiDAR thread error: {e}")

        if running:
            time.sleep(1.0)


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset=\"UTF-8\" />
<title>LiDAR TAS v2</title>
<script src=\"https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js\"></script>
<script src=\"https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js\"></script>
<style>
:root { --bg:#0d1117; --panel:#141b24; --line:#223043; --txt:#dbe7f4; --sub:#8aa0b8; --ok:#23c483; --warn:#e8a43f; --bad:#e65a5a; }
* { box-sizing:border-box; }
body { margin:0; font-family: ui-sans-serif,system-ui,-apple-system; color:var(--txt); background:var(--bg); }
.app { display:grid; grid-template-columns: 1fr 380px; height:100vh; }
#view { width:100%; height:100%; }
.side { border-left:1px solid var(--line); background:var(--panel); padding:12px; overflow:auto; }
.card { border:1px solid var(--line); border-radius:10px; padding:10px; margin-bottom:10px; }
.h { font-size:12px; color:var(--sub); text-transform:uppercase; letter-spacing:.06em; margin-bottom:8px; }
.row { display:flex; gap:8px; margin-bottom:8px; }
.row > * { flex:1; }
input,select,button { width:100%; padding:8px; border-radius:8px; border:1px solid var(--line); background:#0f1622; color:var(--txt); }
button { cursor:pointer; }
button.ok { background:#143628; border-color:#235d46; }
button.warn { background:#3a2e18; border-color:#735a26; }
.kv { display:grid; grid-template-columns: 1fr auto; gap:4px 10px; font-size:13px; }
.kv .k { color:var(--sub); }
.track { font-size:12px; border-top:1px solid var(--line); padding-top:6px; margin-top:6px; }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; background:var(--bad); }
.dot.live { background:var(--ok); box-shadow:0 0 8px var(--ok); }
</style>
</head>
<body>
<div class=\"app\">
  <div id=\"view\"></div>
  <div class=\"side\">
    <div class=\"card\">
      <div class=\"h\"><span id=\"liveDot\" class=\"dot\"></span>Sensor Mode / Reconfigure</div>
      <div class=\"row\">
        <select id=\"modeSel\"></select>
      </div>
      <div class=\"row\">
        <button class=\"ok\" onclick=\"applyMode()\">Apply Mode + Reinit</button>
      </div>
      <div id=\"modeMsg\" style=\"font-size:12px;color:var(--sub);\"></div>
    </div>

    <div class=\"card\">
      <div class=\"h\">Motion Tracking (Background Subtraction)</div>
      <div class=\"row\"><button class=\"warn\" onclick=\"resetBackground()\">Reset Background</button></div>
      <div class=\"row\"><button onclick=\"toggleMotionOnly()\">Toggle Motion-Only View</button></div>
      <div class=\"kv\" id=\"trackList\"></div>
    </div>

    <div class=\"card\">
      <div class=\"h\">TAS Gate</div>
      <div class=\"row\">
        <input id=\"cycleUs\" type=\"number\" value=\"781\" min=\"1\" />
        <input id=\"openUs\" type=\"number\" value=\"150\" min=\"0\" />
      </div>
      <div class=\"row\"><button class=\"ok\" onclick=\"applyGate()\">Apply Gate</button></div>
      <div class=\"row\"><button onclick=\"applyThreeSlot()\">Apply 3-slot 15/751/15</button></div>
      <div id=\"gateMsg\" style=\"font-size:12px;color:var(--sub);\"></div>
    </div>

    <div class=\"card\">
      <div class=\"h\">Realtime Stats</div>
      <div class=\"kv\" id=\"statsKv\"></div>
    </div>
  </div>
</div>

<script>
let scene, camera, renderer, controls, cloud;
let currentFrame = -1;
let motionOnly = false;

function init3d() {
  const v = document.getElementById('view');
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b1017);
  camera = new THREE.PerspectiveCamera(60, v.clientWidth / v.clientHeight, 0.1, 500);
  camera.position.set(15, 12, 16);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(v.clientWidth, v.clientHeight);
  v.appendChild(renderer.domElement);
  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  scene.add(new THREE.GridHelper(40, 40, 0x335577, 0x223344));
  scene.add(new THREE.AxesHelper(2));
  cloud = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial({ size: 0.06, vertexColors: true }));
  scene.add(cloud);
  window.addEventListener('resize', () => {
    camera.aspect = v.clientWidth / v.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(v.clientWidth, v.clientHeight);
  });
  (function loop() {
    requestAnimationFrame(loop);
    controls.update();
    renderer.render(scene, camera);
  })();
}

function updateCloud(points, isMotion) {
  const n = points.length;
  if (!n) return;
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const p = points[i];
    pos[i*3] = p[0];
    pos[i*3+1] = p[2];
    pos[i*3+2] = p[1];
    if (isMotion) {
      col[i*3] = 1.0; col[i*3+1] = 0.3; col[i*3+2] = 0.2;
    } else {
      const h = Math.max(0, Math.min(1, (p[2] + 2) / 6));
      col[i*3] = 0.2 + 0.6*h;
      col[i*3+1] = 0.4 + 0.4*(1-h);
      col[i*3+2] = 0.8 - 0.5*h;
    }
  }
  cloud.geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  cloud.geometry.setAttribute('color', new THREE.BufferAttribute(col, 3));
}

async function refreshModes() {
  const r = await fetch('/api/lidar/config');
  const d = await r.json();
  const sel = document.getElementById('modeSel');
  sel.innerHTML = '';
  (d.supported_modes || []).forEach(m => {
    const o = document.createElement('option');
    o.value = m; o.textContent = m;
    if (m === d.lidar_mode) o.selected = true;
    sel.appendChild(o);
  });
}

async function applyMode() {
  const mode = document.getElementById('modeSel').value;
  const msg = document.getElementById('modeMsg');
  msg.textContent = 'Applying mode and reinitializing sensor...';
  const r = await fetch('/api/lidar/mode', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mode})
  });
  const d = await r.json();
  msg.textContent = d.ok ? `Applied: ${d.lidar_mode}` : `Failed: ${d.error || 'unknown'}`;
  await refreshModes();
}

async function resetBackground() {
  const r = await fetch('/api/motion/reset_background', {method:'POST'});
  const d = await r.json();
  document.getElementById('modeMsg').textContent = d.ok ? 'Background reset requested' : 'Reset failed';
}

function toggleMotionOnly() { motionOnly = !motionOnly; }

async function applyGate() {
  const cycle_us = parseInt(document.getElementById('cycleUs').value);
  const open_us = parseInt(document.getElementById('openUs').value);
  const r = await fetch('/api/gate', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cycle_us, open_us})
  });
  const d = await r.json();
  document.getElementById('gateMsg').textContent = d.ok ? d.desc : `Failed: ${d.error || 'unknown'}`;
}

async function applyThreeSlot() {
  const r = await fetch('/api/gate_multi', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({cycle_us: 781, entries:[{gate:255,duration_us:15},{gate:254,duration_us:751},{gate:255,duration_us:15}]})
  });
  const d = await r.json();
  document.getElementById('gateMsg').textContent = d.ok ? d.desc : `Failed: ${d.error || 'unknown'}`;
}

async function poll() {
  try {
    const [pRes, sRes] = await Promise.all([fetch('/api/points?max=32768'), fetch('/api/stats')]);
    const p = await pRes.json();
    const s = await sRes.json();
    document.getElementById('liveDot').className = 'dot ' + (s.connected ? 'live' : '');

    const points = motionOnly ? (p.motion_points || []) : (p.points || []);
    if (p.frame_id !== currentFrame && points.length) {
      updateCloud(points, motionOnly);
      currentFrame = p.frame_id;
    }

    const kv = document.getElementById('statsKv');
    kv.innerHTML = `
      <div class='k'>mode</div><div>${s.lidar_mode || '-'}</div>
      <div class='k'>fps</div><div>${(s.fps||0).toFixed(2)}</div>
      <div class='k'>completeness</div><div>${((s.frame_completeness||0)*100).toFixed(2)}%</div>
      <div class='k'>pps</div><div>${Math.round(s.pps||0)}</div>
      <div class='k'>gap std</div><div>${(s.gap_stdev_us||0).toFixed(1)} us</div>
      <div class='k'>motion points</div><div>${Math.round(s.motion_points||0)}</div>
      <div class='k'>moving objects</div><div>${Math.round(s.moving_objects||0)}</div>
      <div class='k'>bg ready</div><div>${s.bg_ready ? 'yes' : 'building'}</div>
    `;

    const t = document.getElementById('trackList');
    const tracks = p.tracks || [];
    if (!tracks.length) {
      t.innerHTML = "<div class='k'>tracks</div><div>none</div>";
    } else {
      t.innerHTML = tracks.map(x =>
        `<div class='track'>#${x.id} pts=${x.points} center=(${x.centroid.map(v => v.toFixed(2)).join(', ')})</div>`
      ).join('');
    }
  } catch (_) {}
  setTimeout(poll, 250);
}

init3d();
refreshModes().then(() => poll());
</script>
</body>
</html>
"""


@app.route("/")
def index() -> str:
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/points")
def api_points():
    max_pts = int(flask_request.args.get("max", 32768))
    with lock:
        pts = latest_points or []
        mpts = latest_motion_points or []
        tracks = latest_tracks or []
        fid = latest_frame_id

    if len(pts) > max_pts:
        step = max(1, len(pts) // max_pts)
        pts = pts[::step][:max_pts]
    if len(mpts) > max_pts:
        step = max(1, len(mpts) // max_pts)
        mpts = mpts[::step][:max_pts]

    return jsonify({"points": pts, "motion_points": mpts, "tracks": tracks, "frame_id": fid})


@app.route("/api/stats")
def api_stats():
    d = dict(smoothed_stats)
    d["connected"] = lidar_connected
    d["lidar_mode"] = lidar_state.get("mode", "unknown")
    d["bg_ready"] = motion_cfg["bg_ready"]
    return jsonify(d)


@app.route("/api/lidar/config")
def api_lidar_config():
    try:
        fetch_lidar_config(lidar_state["host"])
    except Exception:
        pass
    return jsonify(
        {
            "lidar_host": lidar_state["host"],
            "lidar_mode": lidar_state.get("mode", "unknown"),
            "udp_profile_lidar": lidar_state.get("udp_profile_lidar", "unknown"),
            "columns_per_packet": lidar_state.get("columns_per_packet", 16),
            "timestamp_mode": lidar_state.get("timestamp_mode", "unknown"),
            "supported_modes": SUPPORTED_LIDAR_MODES,
            "reinit_in_progress": lidar_state.get("sensor_reinit_in_progress", False),
        }
    )


@app.route("/api/lidar/mode", methods=["POST"])
def api_lidar_mode():
    global force_reconnect
    d = flask_request.json or {}
    mode = d.get("mode", "")
    try:
        set_lidar_mode(lidar_state["host"], mode)
        force_reconnect = True
        return jsonify({"ok": True, "lidar_mode": lidar_state.get("mode", mode)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/motion/reset_background", methods=["POST"])
def api_reset_bg():
    reset_motion_background()
    return jsonify({"ok": True})


@app.route("/api/gate", methods=["POST"])
def api_gate():
    d = flask_request.json or {}
    cycle_us = max(1, int(d.get("cycle_us", 1000)))
    open_us = max(0, min(int(d.get("open_us", cycle_us)), cycle_us))
    ok = apply_tas(app.config["KETI_TSN_DIR"], cycle_us, open_us)
    if ok:
        close_us = cycle_us - open_us
        desc = f"cycle={cycle_us}us open={open_us}us close={close_us}us"
        return jsonify({"ok": True, "desc": desc, **tas_state})
    return jsonify({"ok": False, "error": "keti-tsn patch failed"})


@app.route("/api/gate_multi", methods=["POST"])
def api_gate_multi():
    d = flask_request.json or {}
    cycle_us = max(1, int(d.get("cycle_us", 1000)))
    entries = d.get("entries", [])
    if not isinstance(entries, list):
        return jsonify({"ok": False, "error": "entries must be list"})

    try:
        normalized = _normalize_entries(cycle_us, entries)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)})

    ok = apply_tas_entries(app.config["KETI_TSN_DIR"], cycle_us, normalized)
    if not ok:
        return jsonify({"ok": False, "error": "keti-tsn patch failed"})

    open_us = sum(e["duration_us"] for e in normalized if e["gate"] == 255)
    desc = f"cycle={cycle_us}us open={open_us}us entries={len(normalized)}"
    return jsonify({"ok": True, "desc": desc, **tas_state})


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LiDAR TAS web server v2")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8081)
    p.add_argument("--lidar-host", default=DEFAULT_LIDAR_HOST)
    p.add_argument("--lidar-port", type=int, default=DEFAULT_LIDAR_PORT)
    p.add_argument("--keti-tsn-dir", default=DEFAULT_KETI_TSN_DIR)
    p.add_argument("--no-tas-init", action="store_true", help="skip all-open TAS init on startup")
    return p.parse_args()


def main() -> None:
    global running
    args = parse_args()

    lidar_state["host"] = args.lidar_host
    app.config["KETI_TSN_DIR"] = args.keti_tsn_dir

    print("=" * 60)
    print("LiDAR TAS v2")
    print(f"Web UI:      http://127.0.0.1:{args.port}")
    print(f"LiDAR host:  {args.lidar_host}:{args.lidar_port}")
    print(f"KETI TSN:    {args.keti_tsn_dir}")
    print("Features: mode switch + background motion tracking + TAS gate API")
    print("=" * 60)

    if not args.no_tas_init:
        try:
            print("Applying startup TAS all-open (1000/1000)...")
            apply_tas(args.keti_tsn_dir, 1000, 1000)
        except Exception as e:
            print(f"startup TAS init failed: {e}")

    t = threading.Thread(target=lidar_thread, args=(args.lidar_host, args.lidar_port), daemon=True)
    t.start()

    try:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)
    except KeyboardInterrupt:
        running = False


if __name__ == "__main__":
    main()
