#!/usr/bin/env python3
"""AV Engine web UI — upload prompt + files, get video back. Zero AI tokens.

Local:  python3 app.py
Prod:   AV_PASSWORD=yourpass python3 app.py
Open:   http://localhost:5111
"""

import functools
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import (Flask, render_template_string, request, send_file,
                   jsonify, url_for, session, redirect, Response)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB upload limit
app.secret_key = os.environ.get("AV_SECRET_KEY", "dev-secret-change-in-prod")

AV_PASSWORD = os.environ.get("AV_PASSWORD", "")  # empty = no auth (local dev)
MAX_CONCURRENT_JOBS = 1
MAX_DURATION = 30
MAX_STORED_JOBS = 20

PRESETS_DIR = Path(__file__).parent / "presets"
JOBS_DIR = Path(__file__).parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

jobs = {}


# ──────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────

def _check_password(pw):
    if not AV_PASSWORD:
        return True
    return hashlib.sha256(pw.encode()).hexdigest() == hashlib.sha256(AV_PASSWORD.encode()).hexdigest()


def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not AV_PASSWORD:
            return f(*args, **kwargs)
        if session.get("authed"):
            return f(*args, **kwargs)
        return redirect("/login")
    return decorated


LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AV Engine — Login</title>
<style>
  body { background: #0a0a0e; color: #e8e8f0; font-family: -apple-system, sans-serif;
         display: flex; justify-content: center; align-items: center; min-height: 100vh; }
  .box { background: #14141c; border: 1px solid #2a2a3a; border-radius: 12px; padding: 2rem;
         width: 100%; max-width: 360px; text-align: center; }
  h1 { font-size: 1.4rem; margin-bottom: 0.3rem; }
  h1 span { color: #64ff96; }
  .sub { color: #8c8ca0; font-size: 0.8rem; margin-bottom: 1.5rem; }
  input { width: 100%; padding: 0.7rem; background: #0a0a0e; color: #e8e8f0; border: 1px solid #2a2a3a;
          border-radius: 8px; font-size: 1rem; margin-bottom: 0.8rem; text-align: center; }
  input:focus { outline: none; border-color: #64ff96; }
  button { width: 100%; padding: 0.7rem; background: #64ff96; color: #0a0a0e; border: none;
           border-radius: 8px; font-size: 1rem; font-weight: 700; cursor: pointer; }
  .err { color: #ff4060; font-size: 0.85rem; margin-bottom: 0.5rem; }
</style>
</head>
<body>
<div class="box">
  <h1><span>◈</span> AV Engine</h1>
  <p class="sub">blocksage.tech</p>
  {% if error %}<p class="err">{{ error }}</p>{% endif %}
  <form method="POST">
    <input type="password" name="password" placeholder="Access code" autofocus>
    <button type="submit">Enter</button>
  </form>
</div>
</body>
</html>
"""


def cleanup_old_jobs():
    """Remove oldest jobs when over limit."""
    job_dirs = sorted(JOBS_DIR.iterdir(), key=lambda p: p.stat().st_mtime)
    while len(job_dirs) > MAX_STORED_JOBS:
        old = job_dirs.pop(0)
        shutil.rmtree(old, ignore_errors=True)

# ──────────────────────────────────────────────────────────────────────
# HTML template — single page, embedded
# ──────────────────────────────────────────────────────────────────────

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AV Engine</title>
<style>
  :root { --bg: #0a0a0e; --card: #14141c; --border: #2a2a3a; --accent: #64ff96;
          --accent2: #3c8cff; --text: #e8e8f0; --muted: #8c8ca0; --danger: #ff4060; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 2rem 1rem; }
  h1 { font-size: 1.8rem; letter-spacing: 0.05em; margin-bottom: 0.3rem; }
  h1 span { color: var(--accent); }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
          padding: 1.5rem; width: 100%; max-width: 520px; margin-bottom: 1rem; }
  .card h2 { font-size: 1rem; color: var(--accent); margin-bottom: 1rem; letter-spacing: 0.04em; }
  label { display: block; font-size: 0.8rem; color: var(--muted); margin-bottom: 0.3rem; margin-top: 0.8rem; }
  label:first-child { margin-top: 0; }
  input[type=text], input[type=number], textarea, select {
    width: 100%; padding: 0.6rem 0.8rem; background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px; font-size: 0.9rem; }
  input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); }
  textarea { resize: vertical; min-height: 60px; font-family: inherit; }
  input[type=color] { width: 48px; height: 32px; border: 1px solid var(--border);
                       border-radius: 6px; background: var(--bg); cursor: pointer; padding: 2px; }
  .color-row { display: flex; gap: 0.8rem; flex-wrap: wrap; }
  .color-item { display: flex; flex-direction: column; align-items: center; gap: 0.2rem; }
  .color-item span { font-size: 0.7rem; color: var(--muted); }
  input[type=range] { width: 100%; accent-color: var(--accent); }
  .range-row { display: flex; align-items: center; gap: 0.8rem; }
  .range-val { font-size: 0.85rem; min-width: 2.5rem; text-align: center; color: var(--accent); }
  .file-drop { border: 2px dashed var(--border); border-radius: 10px; padding: 1.5rem; text-align: center;
               color: var(--muted); cursor: pointer; transition: border-color 0.2s; }
  .file-drop:hover, .file-drop.dragover { border-color: var(--accent); color: var(--text); }
  .file-drop input { display: none; }
  .file-drop .file-name { color: var(--accent); font-size: 0.85rem; margin-top: 0.5rem; }
  .checks { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 0.5rem; }
  .checks label { display: flex; align-items: center; gap: 0.3rem; margin: 0; cursor: pointer; font-size: 0.85rem; color: var(--text); }
  button[type=submit] { width: 100%; padding: 0.8rem; background: var(--accent); color: var(--bg);
                         border: none; border-radius: 10px; font-size: 1rem; font-weight: 700;
                         letter-spacing: 0.04em; cursor: pointer; margin-top: 1rem; transition: opacity 0.2s; }
  button[type=submit]:hover { opacity: 0.9; }
  button[type=submit]:disabled { opacity: 0.4; cursor: not-allowed; }
  #status { width: 100%; max-width: 520px; text-align: center; padding: 1rem; }
  #status .spinner { display: inline-block; width: 20px; height: 20px; border: 3px solid var(--border);
                     border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #status .msg { margin-top: 0.5rem; font-size: 0.9rem; }
  #status a { color: var(--accent); text-decoration: none; font-weight: 600; }
  #status a:hover { text-decoration: underline; }
  .preset-tag { display: inline-block; padding: 0.15rem 0.5rem; background: var(--accent); color: var(--bg);
                border-radius: 4px; font-size: 0.7rem; font-weight: 700; margin-left: 0.3rem; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; }
  .motif-checks label { font-size: 0.8rem; }
</style>
</head>
<body>

<h1><span>◈</span> AV Engine</h1>
<p class="subtitle">Upload · Configure · Render · Download — zero AI tokens</p>

<form id="form" enctype="multipart/form-data">

<!-- PRESET -->
<div class="card">
  <h2>① Preset</h2>
  <label for="preset">Base preset</label>
  <select id="preset" name="preset">
    <option value="generative_vibes">Generative Vibes (no input needed)</option>
    <option value="cosmic_chaos">Cosmic Chaos (video remix)</option>
    <option value="explainer">Brand Explainer</option>
  </select>
</div>

<!-- SOURCE -->
<div class="card">
  <h2>② Source Files <span style="color:var(--muted);font-size:0.75rem;font-weight:400">(optional)</span></h2>
  <label>Video (will extract frames + audio)</label>
  <div class="file-drop" id="videoDrop">
    <div>Drop video here or click to browse</div>
    <input type="file" name="video" accept="video/*" id="videoInput">
    <div class="file-name" id="videoName"></div>
  </div>
  <label>Images (will be used as source material)</label>
  <div class="file-drop" id="imageDrop">
    <div>Drop images here or click to browse</div>
    <input type="file" name="images" accept="image/*" multiple id="imageInput">
    <div class="file-name" id="imageNames"></div>
  </div>
</div>

<!-- TEXT -->
<div class="card">
  <h2>③ Text & Vibes</h2>
  <label for="vibes">Vibes text (one per line — shown as overlays)</label>
  <textarea id="vibes" name="vibes" rows="4" placeholder="GOOD VIBES ONLY&#10;✨🌙☀️&#10;POSITIVITY ∞"></textarea>
</div>

<!-- COLORS -->
<div class="card">
  <h2>④ Colors</h2>
  <div class="color-row">
    <div class="color-item"><input type="color" name="c_bg" value="#0a0a12"><span>bg</span></div>
    <div class="color-item"><input type="color" name="c_primary" value="#2864ff"><span>primary</span></div>
    <div class="color-item"><input type="color" name="c_secondary" value="#ff3c3c"><span>secondary</span></div>
    <div class="color-item"><input type="color" name="c_accent" value="#ffd700"><span>accent</span></div>
    <div class="color-item"><input type="color" name="c_highlight" value="#e6e6f5"><span>highlight</span></div>
  </div>
</div>

<!-- SETTINGS -->
<div class="card">
  <h2>⑤ Settings</h2>
  <div class="row2">
    <div>
      <label for="duration">Duration (seconds)</label>
      <div class="range-row">
        <input type="range" id="duration" name="duration" min="5" max="30" value="15">
        <span class="range-val" id="durVal">15</span>
      </div>
    </div>
    <div>
      <label for="seed">Seed</label>
      <input type="number" id="seed" name="seed" value="2026" min="1">
    </div>
  </div>
  <div class="row2" style="margin-top: 0.8rem">
    <div>
      <label for="width">Width</label>
      <select id="width" name="width">
        <option value="720">720</option>
        <option value="1080" selected>1080</option>
      </select>
    </div>
    <div>
      <label for="height">Height</label>
      <select id="height" name="height">
        <option value="1280" selected>1280</option>
        <option value="1920">1920</option>
      </select>
    </div>
  </div>
  <label>Chaos level</label>
  <div class="range-row">
    <input type="range" id="chaos" name="chaos" min="0" max="100" value="70">
    <span class="range-val" id="chaosVal">70%</span>
  </div>
  <label style="margin-top:0.8rem">Motifs</label>
  <div class="checks motif-checks">
    <label><input type="checkbox" name="motif_square" checked> 🟦 Square</label>
    <label><input type="checkbox" name="motif_circle" checked> ⭕ Circle</label>
    <label><input type="checkbox" name="motif_constellation" checked> ✦ Constellation</label>
    <label><input type="checkbox" name="motif_polygons" checked> △ Polygons</label>
  </div>
  <label style="margin-top:0.8rem">Audio</label>
  <div class="checks">
    <label><input type="radio" name="audio_mode" value="synth" checked> Synth only</label>
    <label><input type="radio" name="audio_mode" value="mix"> Mix with original</label>
    <label><input type="radio" name="audio_mode" value="original"> Original only</label>
  </div>
</div>

<button type="submit" id="submitBtn">Render Video</button>
</form>

<div id="status"></div>

<script>
// File drop zones
function setupDrop(dropId, inputId, nameId, multi) {
  const drop = document.getElementById(dropId);
  const inp = document.getElementById(inputId);
  const nameEl = document.getElementById(nameId);
  drop.addEventListener('click', () => inp.click());
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', e => {
    e.preventDefault(); drop.classList.remove('dragover');
    inp.files = e.dataTransfer.files;
    showNames();
  });
  inp.addEventListener('change', showNames);
  function showNames() {
    const names = Array.from(inp.files).map(f => f.name).join(', ');
    nameEl.textContent = names || '';
  }
}
setupDrop('videoDrop', 'videoInput', 'videoName', false);
setupDrop('imageDrop', 'imageInput', 'imageNames', true);

// Range display
document.getElementById('duration').addEventListener('input', e => {
  document.getElementById('durVal').textContent = e.target.value;
});
document.getElementById('chaos').addEventListener('input', e => {
  document.getElementById('chaosVal').textContent = e.target.value + '%';
});

// Preset → resolution defaults
document.getElementById('preset').addEventListener('change', e => {
  if (e.target.value === 'generative_vibes') {
    document.getElementById('width').value = '720';
    document.getElementById('height').value = '1280';
  } else {
    document.getElementById('width').value = '1080';
    document.getElementById('height').value = '1920';
  }
});

// Submit
document.getElementById('form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  const status = document.getElementById('status');
  btn.disabled = true;
  status.innerHTML = '<div class="spinner"></div><div class="msg">Uploading...</div>';

  const fd = new FormData(e.target);
  try {
    const res = await fetch('/render', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.job_id) {
      pollJob(data.job_id);
    } else {
      status.innerHTML = '<div class="msg" style="color:var(--danger)">Error: ' + (data.error || 'Unknown') + '</div>';
      btn.disabled = false;
    }
  } catch (err) {
    status.innerHTML = '<div class="msg" style="color:var(--danger)">Upload failed: ' + err.message + '</div>';
    btn.disabled = false;
  }
});

async function pollJob(jobId) {
  const status = document.getElementById('status');
  const btn = document.getElementById('submitBtn');
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const res = await fetch('/status/' + jobId);
      const data = await res.json();
      if (data.status === 'done') {
        status.innerHTML = '<div class="msg">✅ Done! (' + (data.elapsed || '?') + 's)<br><br>' +
          '<a href="/download/' + jobId + '">⬇ Download Video (' + (data.size_mb || '?') + ' MB)</a></div>';
        btn.disabled = false;
        return;
      } else if (data.status === 'error') {
        status.innerHTML = '<div class="msg" style="color:var(--danger)">Render failed: ' + (data.error || 'Unknown') + '</div>';
        btn.disabled = false;
        return;
      } else {
        status.innerHTML = '<div class="spinner"></div><div class="msg">' + (data.message || 'Rendering...') + '</div>';
      }
    } catch (err) {
      // keep polling
    }
  }
}
</script>
</body>
</html>
"""


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def hex_to_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i+2], 16) for i in (0, 2, 4)]


def build_config_from_form(form, files, job_dir):
    preset_name = form.get("preset", "generative_vibes")
    preset_path = PRESETS_DIR / f"{preset_name}.json"
    if preset_path.exists():
        with open(preset_path) as f:
            cfg = json.load(f)
    else:
        cfg = {}

    meta = cfg.setdefault("meta", {})
    meta["seed"] = int(form.get("seed", 2026))
    meta["duration"] = int(form.get("duration", 15))
    meta["width"] = int(form.get("width", 1080))
    meta["height"] = int(form.get("height", 1920))
    meta["fps"] = 30
    meta["output"] = str(job_dir / "output.mp4")

    # Sources
    sources = cfg.setdefault("sources", {})
    video_file = files.get("video")
    if video_file and video_file.filename:
        vid_path = job_dir / "input_video" / video_file.filename
        vid_path.parent.mkdir(exist_ok=True)
        video_file.save(str(vid_path))
        sources["video"] = str(vid_path)
        if cfg.get("audio", {}).get("mode") != "original":
            cfg.setdefault("audio", {})["mode"] = form.get("audio_mode", "mix")
    else:
        sources["video"] = None
        cfg.setdefault("audio", {})["mode"] = form.get("audio_mode", "synth")

    image_files = files.getlist("images")
    img_paths = []
    if image_files:
        img_dir = job_dir / "input_images"
        img_dir.mkdir(exist_ok=True)
        for img_file in image_files:
            if img_file.filename:
                p = img_dir / img_file.filename
                img_file.save(str(p))
                img_paths.append(str(p))
    sources["images"] = img_paths

    # Colors
    colors = cfg.setdefault("colors", {})
    for key in ["bg", "primary", "secondary", "accent", "highlight"]:
        val = form.get(f"c_{key}")
        if val:
            colors[key] = hex_to_rgb(val)

    # Vibes
    vibes_text = form.get("vibes", "").strip()
    if vibes_text:
        vibes = [line.strip() for line in vibes_text.split("\n") if line.strip()]
        cfg.setdefault("text", {})["vibes"] = vibes
        # Distribute vibes across timeline acts as texts
        for i, act in enumerate(cfg.get("timeline", [])):
            if vibes:
                act["texts"] = [vibes[j % len(vibes)] if j % 2 == 0 else "" for j in range(4)]

    # Chaos level → intensity scaling
    chaos = int(form.get("chaos", 70)) / 100.0
    for act in cfg.get("timeline", []):
        lo, hi = act.get("intensity", [0.3, 0.7])
        act["intensity"] = [lo * chaos / 0.7, min(1.0, hi * chaos / 0.7)]

    # Motifs
    motifs = cfg.setdefault("motifs", {"enabled": True, "items": []})
    items = []
    if form.get("motif_square"):
        items.append({"type": "orbiting_square", "color": "primary", "size": 60, "orbit_radius": 300, "speed": 1.5})
    if form.get("motif_circle"):
        items.append({"type": "orbiting_circle", "color": "secondary", "size": 40, "orbit_radius": 250, "speed": 2.0})
    if form.get("motif_constellation"):
        items.append({"type": "constellation", "color": "highlight", "n_stars": 6, "radius": 100})
    if form.get("motif_polygons"):
        items.append({"type": "drifting_polygons", "count": 3, "sides": 3, "colors": ["primary", "secondary", "accent"]})
    motifs["enabled"] = len(items) > 0
    motifs["items"] = items

    return cfg


# ──────────────────────────────────────────────────────────────────────
# Render worker (runs in background thread)
# ──────────────────────────────────────────────────────────────────────

def render_worker(job_id, cfg):
    import importlib.util
    try:
        jobs[job_id]["status"] = "rendering"
        jobs[job_id]["message"] = "Loading engine..."

        engine_path = Path(__file__).parent / "engine.py"
        spec = importlib.util.spec_from_file_location("engine", engine_path)
        engine = importlib.util.module_from_spec(spec)

        # Redirect engine's cwd
        original_cwd = os.getcwd()
        os.chdir(str(Path(__file__).parent))

        jobs[job_id]["message"] = "Rendering frames..."
        t0 = time.time()
        spec.loader.exec_module(engine)
        engine.render(cfg)
        elapsed = time.time() - t0

        os.chdir(original_cwd)

        out_path = Path(cfg["meta"]["output"])
        if out_path.exists():
            size_mb = round(out_path.stat().st_size / 1024 / 1024, 1)
            jobs[job_id]["status"] = "done"
            jobs[job_id]["elapsed"] = round(elapsed, 1)
            jobs[job_id]["size_mb"] = size_mb
            jobs[job_id]["output"] = str(out_path)
        else:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Output file not created"

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)[:500]


# ──────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if not AV_PASSWORD:
        return redirect("/")
    if request.method == "POST":
        pw = request.form.get("password", "")
        if _check_password(pw):
            session["authed"] = True
            return redirect("/", code=303)
        return render_template_string(LOGIN_HTML, error="Wrong access code"), 401
    return render_template_string(LOGIN_HTML, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
@require_auth
def index():
    return render_template_string(HTML)


@app.route("/render", methods=["POST"])
@require_auth
def start_render():
    # Rate limit: only one render at a time
    active = sum(1 for j in jobs.values() if j.get("status") in ("queued", "rendering"))
    if active >= MAX_CONCURRENT_JOBS:
        return jsonify({"error": "A render is already in progress. Please wait."}), 429

    # Cap duration
    dur = min(int(request.form.get("duration", 15)), MAX_DURATION)

    cleanup_old_jobs()

    job_id = str(uuid.uuid4())[:8]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        cfg = build_config_from_form(request.form, request.files, job_dir)
        cfg["meta"]["duration"] = dur
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    with open(job_dir / "config.json", "w") as f:
        json.dump(cfg, f, indent=2, default=str)

    jobs[job_id] = {"status": "queued", "message": "Starting..."}
    t = threading.Thread(target=render_worker, args=(job_id, cfg), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
@require_auth
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "error", "error": "Job not found"}), 404
    return jsonify(job)


@app.route("/download/<job_id>")
@require_auth
def download(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return "Not ready", 404
    out_path = job.get("output")
    if not out_path or not Path(out_path).exists():
        return "File not found", 404
    return send_file(out_path, as_attachment=True, download_name=f"av_{job_id}.mp4")


# ──────────────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5111))
    print(f"\n  ◈ AV Engine Web UI")
    print(f"  http://localhost:{port}")
    if AV_PASSWORD:
        print(f"  Password protection: ON")
    else:
        print(f"  Password protection: OFF (set AV_PASSWORD env var)")
    print()
    app.run(host="0.0.0.0", port=port, debug=False)
