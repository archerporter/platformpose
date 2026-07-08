"""
PlatformPose — Flask + HTMX + SQLite server
Run:  python flask_app/app.py
"""
import os
from dotenv import load_dotenv
import sys
import json
import sqlite3
import subprocess
import threading
import time
import webbrowser
from datetime import datetime

import pandas as pd
from flask import (
    Flask, render_template, request, jsonify,
    send_file, redirect, url_for, after_this_request,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE        = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_HERE)
SETTINGS_FILE = os.path.join(_HERE, 'settings.json')
TASK_FILE     = os.path.join(PROJECT_ROOT, 'pose_landmarker_heavy.task')
REGION_SELECTOR = os.path.join(PROJECT_ROOT, 'region_selector.py')
REGION_RESULT   = os.path.join(PROJECT_ROOT, 'region_result.json')
CONTROL_PANEL   = os.path.join(_HERE, 'control_panel.py')


app = Flask(__name__)

# ── Settings helpers ──────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    'project':         'my_project',
    'video_id':        'video_001',
    'researcher':      '',
    'notes':           '',
    'min_visibility':  0.5,
    'max_out_of_range': 5,
    'floor_method':    'Rolling Minimum',
    'window_size':     30,
    'left':   0,
    'top':    0,
    'width':  800,
    'height': 600,
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            s = json.load(f)
        # Fill in any missing keys
        for k, v in DEFAULT_SETTINGS.items():
            s.setdefault(k, v)
        return s
    return DEFAULT_SETTINGS.copy()

def save_settings(data: dict):
    s = load_settings()
    s.update(data)
    # Coerce numeric fields
    for key in ('left', 'top', 'width', 'height', 'window_size', 'max_out_of_range'):
        if key in s:
            s[key] = int(s[key])
    for key in ('min_visibility',):
        if key in s:
            s[key] = float(s[key])
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(s, f, indent=2)
    return s

# ── Database helpers ───────────────────────────────────────────────────────────
def db_path(project: str) -> str:
    return os.path.join(PROJECT_ROOT, f'{project}.db')

def get_conn(project: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(project))
    conn.row_factory = sqlite3.Row
    return conn

def ensure_table(project: str):
    lm_cols = '\n'.join(
        f'    lm{i}_{c} REAL,'
        for i in range(33)
        for c in ('x', 'y', 'z', 'vis')
    )
    ddl = f"""
    CREATE TABLE IF NOT EXISTS frames (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id    TEXT    NOT NULL,
        frame       INTEGER NOT NULL,
        timestamp   REAL,
        researcher  TEXT,
        project     TEXT,
        notes       TEXT,
        floor_y     REAL,
        captured_at TEXT    DEFAULT (datetime('now')),
{lm_cols}
        _pad        INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_video_id ON frames (video_id);
    CREATE INDEX IF NOT EXISTS idx_project  ON frames (project);
    """
    with get_conn(project) as conn:
        conn.executescript(ddl)

def corpus_summary(project: str) -> list[dict]:
    """Return per-video summary rows including retention stats."""
    path = db_path(project)
    if not os.path.exists(path):
        return []
    try:
        with get_conn(project) as conn:
            rows = conn.execute("""
                SELECT
                    video_id,
                    COUNT(*)                                        AS frames,
                    ROUND(MAX(timestamp), 2)                        AS duration_s,
                    MIN(captured_at)                                AS first_seen,
                    MAX(frame) + 1                                  AS total_attempted,
                    ROUND(COUNT(*) * 100.0 / (MAX(frame) + 1), 1)  AS retention_pct
                FROM frames
                GROUP BY video_id
                ORDER BY first_seen DESC
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []

def video_frame_count(project: str, video_id: str) -> int:
    """Return how many frames already exist for video_id in project."""
    path = db_path(project)
    if not os.path.exists(path):
        return 0
    try:
        with get_conn(project) as conn:
            return conn.execute(
                'SELECT COUNT(*) FROM frames WHERE video_id = ?', (video_id,)
            ).fetchone()[0]
    except Exception:
        return 0

def total_frames(project: str) -> int:
    path = db_path(project)
    if not os.path.exists(path):
        return 0
    try:
        with get_conn(project) as conn:
            return conn.execute('SELECT COUNT(*) FROM frames').fetchone()[0]
    except Exception:
        return 0

# ── Region helpers ─────────────────────────────────────────────────────────────
_region_proc   = {}   # single-slot: pid, proc
_control_proc  = {}   # single-slot: pid, proc

def load_region() -> dict | None:
    if os.path.exists(REGION_RESULT):
        try:
            with open(REGION_RESULT) as f:
                return json.load(f)
        except Exception:
            pass
    return None

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    settings = load_settings()
    region   = load_region()
    summary  = corpus_summary(settings['project'])
    n_frames = total_frames(settings['project'])
    return render_template(
        'index.html',
        s=settings,
        project=settings['project'],
        region=region,
        summary=summary,
        n_frames=n_frames,
        task_ok=os.path.exists(TASK_FILE),
        db_exists=os.path.exists(db_path(settings['project'])),
    )

@app.route('/about')
def about():
    return render_template('about.html')

# ── API: settings ──────────────────────────────────────────────────────────────
@app.route('/api/settings', methods=['POST'])
def api_settings():
    data = request.form.to_dict()
    save_settings(data)
    return ('', 204)

# ── API: region selector ───────────────────────────────────────────────────────
@app.route('/api/launch-region-selector', methods=['POST'])
def api_launch_region():
    import sys, time
    mtime_before = os.path.getmtime(REGION_RESULT) if os.path.exists(REGION_RESULT) else 0

    COUNTDOWN = 3          # seconds shown in the browser countdown
    DELAY     = COUNTDOWN + 0.5   # subprocess waits slightly longer for buffer

    proc = subprocess.Popen(
        [sys.executable, REGION_SELECTOR, '--delay', str(DELAY)],
        cwd=PROJECT_ROOT,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    _region_proc['pid']  = proc.pid
    _region_proc['proc'] = proc

    # Brief wait — catch import-level crashes before returning the countdown UI
    time.sleep(0.4)
    if proc.poll() is not None:
        lines = proc.stderr.read().decode('utf-8', errors='replace').splitlines()
        error = '\n'.join(l for l in lines if not l.startswith('objc[')) or \
                f'Subprocess exited with code {proc.returncode}'
        return render_template(
            'partials/region_status.html',
            polling=False, region=None, error=error,
        )

    return render_template(
        'partials/region_status.html',
        polling=True,
        pid=proc.pid,
        mtime_before=mtime_before,
        countdown=COUNTDOWN,
        region=load_region(),
    )

@app.route('/api/region-status')
def api_region_status():
    mtime_before = float(request.args.get('mtime_before', 0))
    current_mtime = os.path.getmtime(REGION_RESULT) if os.path.exists(REGION_RESULT) else 0
    done = current_mtime > mtime_before
    region = load_region() if done else None

    # Also save coordinates back to settings if done
    if done and region:
        save_settings({
            'left':   region.get('left', 0),
            'top':    region.get('top', 0),
            'width':  region.get('width', 800),
            'height': region.get('height', 600),
        })

    return render_template(
        'partials/region_status.html',
        polling=not done,
        pid=request.args.get('pid'),
        mtime_before=mtime_before,
        region=region,
        just_captured=done,
    )

# ── API: launch control panel ──────────────────────────────────────────────────
@app.route('/api/launch-control-panel', methods=['POST'])
def api_launch_control_panel():
    import sys, tempfile
    data = request.form.to_dict()
    # Pop action flags before saving — they're ephemeral and must not persist
    do_replace = data.pop('replace', None) == 'true'
    do_force   = data.pop('force',   None) == 'true'
    settings   = save_settings(data)

    # Warn if video_id already has frames, unless the user confirmed
    project  = settings.get('project', 'my_project')
    video_id = settings.get('video_id', 'video')

    if do_replace:
        # Delete existing frames for this video_id before launching
        with get_conn(project) as conn:
            conn.execute(
                'DELETE FROM frames WHERE video_id = ?', (video_id,)
            )
    elif not do_force:
        existing = video_frame_count(project, video_id)
        if existing:
            return render_template('partials/launch_status.html',
                                   state='duplicate_video',
                                   video_id=video_id,
                                   existing_frames=existing)

    # Write a temp settings file for the subprocess
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False,
        dir=_HERE,
    )
    json.dump(settings, tmp)
    tmp.close()

    error = None
    try:
        proc = subprocess.Popen(
            [sys.executable, CONTROL_PANEL, tmp.name],
            cwd=PROJECT_ROOT,
        )
        pid = proc.pid
        _control_proc['proc'] = proc
        _control_proc['pid']  = pid

        # macOS: bring control panel window to front
        if sys.platform == 'darwin':
            subprocess.Popen([
                'osascript', '-e',
                'tell application "System Events" to set frontmost of '
                '(first process whose unix id is ' + str(pid) + ') to true',
            ])
    except Exception as e:
        error = str(e)
        pid = None

    if error:
        return render_template('partials/launch_status.html',
                               state='error', error=error)

    project = settings.get('project', 'my_project')
    return render_template('partials/launch_status.html',
                           state='active', pid=pid, project=project)


# ── API: capture status (polled by launch_status partial) ─────────────────────
@app.route('/api/capture-status')
def api_capture_status():
    proc    = _control_proc.get('proc')
    project = load_settings().get('project', 'my_project')

    if proc is None:
        # No session started this server run
        return render_template('partials/launch_status.html', state='idle')

    if proc.poll() is None:
        # Still running
        return render_template('partials/launch_status.html',
                               state='active',
                               pid=_control_proc.get('pid'),
                               project=project)

    # Exited — session complete
    _control_proc.clear()
    return render_template('partials/launch_status.html',
                           state='complete', project=project)

# ── API: corpus ────────────────────────────────────────────────────────────────
@app.route('/api/corpus')
def api_corpus():
    project  = request.args.get('project') or load_settings().get('project', 'my_project')
    summary  = corpus_summary(project)
    n_frames = total_frames(project)
    db_exists = os.path.exists(db_path(project))
    return render_template(
        'partials/corpus.html',
        summary=summary,
        n_frames=n_frames,
        project=project,
        db_exists=db_exists,
    )

# ── Visualizer ────────────────────────────────────────────────────────────────
@app.route('/visualize')
def visualize():
    project  = request.args.get('project') or load_settings().get('project', 'my_project')
    video_id = request.args.get('video_id', '')
    videos   = [r['video_id'] for r in corpus_summary(project)]
    return render_template('visualize.html',
                           project=project,
                           video_id=video_id,
                           videos=videos)

@app.route('/api/frames')
def api_frames():
    project  = request.args.get('project') or load_settings().get('project', 'my_project')
    video_id = request.args.get('video_id', '')
    if not video_id:
        return jsonify({'error': 'video_id required'}), 400

    path = db_path(project)
    if not os.path.exists(path):
        return jsonify({'error': 'Project database not found'}), 404

    settings = load_settings()

    try:
        with get_conn(project) as conn:
            # Order by id (autoincrement insertion order) so that multiple
            # capture sessions of the same video_id play back in the order
            # they were recorded, not interleaved by overlapping timestamps.
            rows = conn.execute(
                'SELECT * FROM frames WHERE video_id = ? ORDER BY id',
                (video_id,)
            ).fetchall()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if not rows:
        return jsonify({'error': 'No frames found for this video'}), 404

    # Stitch session timestamps: each capture session resets to t=0.
    # Detect resets (timestamp decreases by >1 s) and add a running offset
    # so the final timeline is monotonically increasing.
    frames    = []
    t_offset  = 0.0
    prev_raw  = -1.0
    AVG_GAP   = 1.0 / 3.5   # estimated inter-frame gap (≈ 3.5 fps)

    for row in rows:
        t_raw = row['timestamp']
        if t_raw < prev_raw - 1.0:          # timestamp reset → new session
            t_offset += prev_raw + AVG_GAP  # continue from where last session ended
        prev_raw = t_raw

        lms = []
        for i in range(33):
            lms.append([
                round(row[f'lm{i}_x'], 5),
                round(row[f'lm{i}_y'], 5),
                round(row[f'lm{i}_z'], 4),   # index 2 — hip-relative depth
                round(row[f'lm{i}_vis'], 3), # index 3
            ])
        frames.append({'t': round(t_raw + t_offset, 4), 'lms': lms})

    # Recompute floor_y from the full set of foot landmarks rather than
    # trusting the stored column (which may come from older, less accurate
    # captures).  Using all six foot points — ankle (27/28), heel (29/30),
    # and toe tip (31/32) — captures the actual contact surface, not just
    # the ankle joint height.  In image coords y increases downward.
    # 99th-percentile is robust to airborne frames while aligning the line
    # with the true floor contact level.
    foot_ys = []
    for row in rows:
        ys = [row[f'lm{i}_y'] or 0.0 for i in (27, 28, 29, 30, 31, 32)]
        foot_ys.append(max(ys))

    if foot_ys:
        foot_ys.sort()
        p99 = foot_ys[min(int(0.99 * len(foot_ys)), len(foot_ys) - 1)]
        floor_y = round(p99, 5)
    else:
        floor_y = None

    # Compute retention stats with session-boundary awareness.
    # `frame` is a per-session screenshot index (resets to 0 each capture
    # session), so we accumulate per-session maxima rather than global MAX.
    total_attempted = 0
    session_max     = 0
    prev_raw_ta     = -1.0
    for row in rows:
        t_raw = row['timestamp']
        if t_raw < prev_raw_ta - 1.0:      # session boundary detected
            total_attempted += session_max + 1
            session_max = 0
        session_max  = max(session_max, row['frame'])
        prev_raw_ta  = t_raw
    total_attempted += session_max + 1      # flush the last session

    clean_frames      = len(frames)
    retention_rate    = round(clean_frames / total_attempted * 100, 1) if total_attempted else 0.0
    out_of_frame_rate = round(100.0 - retention_rate, 1)

    # Z range across body landmarks (11-32, skip noisy face points) for
    # normalizing the depth encoding in the visualizer.
    z_vals = []
    for row in rows:
        for i in range(11, 33):
            z = row[f'lm{i}_z']
            if z is not None:
                z_vals.append(z)
    z_min = round(min(z_vals), 4) if z_vals else -0.3
    z_max = round(max(z_vals), 4) if z_vals else  0.3

    return jsonify({
        'video_id':          video_id,
        'project':           project,
        'frame_count':       clean_frames,
        'total_attempted':   total_attempted,
        'clean_frames':      clean_frames,
        'retention_rate':    retention_rate,
        'out_of_frame_rate': out_of_frame_rate,
        'z_min':             z_min,
        'z_max':             z_max,
        'duration_s':        round(frames[-1]['t'], 3) if frames else 0,
        'region_width':      settings.get('width',  418),
        'region_height':     settings.get('height', 750),
        'floor_y':           floor_y,
        'frames':            frames,
    })

# ── API: export CSV ────────────────────────────────────────────────────────────
@app.route('/api/export-csv')
def api_export_csv():
    import tempfile
    project = request.args.get('project') or load_settings().get('project', 'my_project')
    path = db_path(project)
    if not os.path.exists(path):
        return ('No corpus found for this project.', 404)

    with get_conn(project) as conn:
        df = pd.read_sql_query('SELECT * FROM frames ORDER BY id', conn)

    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.csv', delete=False, dir=_HERE,
    )
    df.to_csv(tmp.name, index=False)
    tmp.close()

    return send_file(
        tmp.name,
        as_attachment=True,
        download_name=f'{project}_corpus_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
        mimetype='text/csv',
    )


# ── API: export skeleton video ─────────────────────────────────────────────────

# Segment pairs and colors mirror the JS visualizer exactly.
_VID_SEGS = {
    'center': [(0,1),(1,2),(2,3),(3,7),(0,4),(4,5),(5,6),(6,8),(9,10),(11,12),(23,24)],
    'left':   [(11,13),(13,15),(15,17),(15,19),(15,21),(17,19),
               (11,23),(23,25),(25,27),(27,29),(27,31),(29,31)],
    'right':  [(12,14),(14,16),(16,18),(16,20),(16,22),(18,20),
               (12,24),(24,26),(26,28),(28,30),(28,32),(30,32)],
}
_VID_CLR = {            # BGR (OpenCV reverses RGB channels)
    'center': (175, 163, 156),   # gray-400
    'left':   (250, 165,  96),   # blue-400
    'right':  (113, 113, 248),   # red-400
}
_VID_BG      = (39, 24, 17)     # #111827 in BGR
_VID_LM_SIDE = [
    'left'   if i in {1,2,3,7,9,11,13,15,17,19,21,23,25,27,29,31} else
    'right'  if i in {4,5,6,8,10,12,14,16,18,20,22,24,26,28,30,32} else
    'center'
    for i in range(33)
]


def _vid_blend(color_bgr, alpha):
    """Pre-blend a BGR color against the video background at the given alpha."""
    return tuple(int(alpha * c + (1.0 - alpha) * b)
                 for c, b in zip(color_bgr, _VID_BG))


def _render_skel(lms, floor_y, W, H):
    """Render one skeleton frame to a (H, W, 3) numpy BGR image."""
    import cv2
    import numpy as np

    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[...] = _VID_BG

    # Dashed floor line
    if floor_y is not None:
        fy   = int(floor_y * H)
        dash = max(8, W // 50)
        gap  = max(5, W // 70)
        fc   = _vid_blend((128, 114, 107), 0.55)   # #6b7280 at 55% opacity
        x = 0
        while x < W:
            cv2.line(img, (x, fy), (min(x + dash, W - 1), fy), fc, 1)
            x += dash + gap

    # Connections
    for side, pairs in _VID_SEGS.items():
        base = _VID_CLR[side]
        for ai, bi in pairs:
            la, lb = lms[ai], lms[bi]
            alpha  = min(la[3], lb[3]) * 0.88
            if alpha < 0.04:
                continue
            cv2.line(img,
                     (int(la[0] * W), int(la[1] * H)),
                     (int(lb[0] * W), int(lb[1] * H)),
                     _vid_blend(base, alpha), 2)

    # Joints
    for i in range(33):
        lm = lms[i]
        if lm[3] < 0.04:
            continue
        cv2.circle(img,
                   (int(lm[0] * W), int(lm[1] * H)), 4,
                   _vid_blend(_VID_CLR[_VID_LM_SIDE[i]], min(lm[3] + 0.1, 1.0)),
                   -1)
    return img


@app.route('/api/export-video')
def api_export_video():
    import cv2, tempfile

    project  = request.args.get('project', '').strip()
    video_id = request.args.get('video_id', '').strip()
    if not project or not video_id:
        return jsonify({'error': 'project and video_id required'}), 400

    path = db_path(project)
    if not os.path.exists(path):
        return jsonify({'error': 'project not found'}), 404

    try:
        with get_conn(project) as conn:
            rows = conn.execute(
                'SELECT * FROM frames WHERE video_id = ? ORDER BY id',
                (video_id,)
            ).fetchall()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if not rows:
        return jsonify({'error': 'no frames found for this video'}), 404

    # Stitch timestamps across capture sessions (same logic as api_frames)
    frames   = []
    t_offset = 0.0
    prev_raw = -1.0
    AVG_GAP  = 1.0 / 3.5

    for row in rows:
        t_raw = row['timestamp']
        if t_raw < prev_raw - 1.0:
            t_offset += prev_raw + AVG_GAP
        prev_raw = t_raw
        lms = [[row[f'lm{i}_{c}'] or 0.0 for c in ('x', 'y', 'z', 'vis')]
               for i in range(33)]
        frames.append({'t': t_raw + t_offset, 'lms': lms})

    # Floor y — 99th percentile of foot landmarks
    foot_ys = sorted(
        max(row[f'lm{i}_y'] or 0.0 for i in (27, 28, 29, 30, 31, 32))
        for row in rows
    )
    floor_y = foot_ys[min(int(0.99 * len(foot_ys)), len(foot_ys) - 1)]

    # Output dimensions — capture region scaled to 540 px wide, even numbers
    settings = load_settings()
    rw = settings.get('width',  418)
    rh = settings.get('height', 750)
    W  = 540
    H  = round(W * rh / rw)
    W += W % 2
    H += H % 2

    FPS      = 30
    duration = frames[-1]['t']
    n_out    = max(1, round(duration * FPS))

    tmp_path = tempfile.mktemp(suffix='.mp4', dir=_HERE)
    fourcc   = cv2.VideoWriter_fourcc(*'mp4v')
    vw       = cv2.VideoWriter(tmp_path, fourcc, FPS, (W, H))
    if not vw.isOpened():
        return jsonify({'error': 'OpenCV VideoWriter could not open — codec unavailable'}), 500

    # Map each output frame to its nearest data frame by timestamp
    data_i = 0
    for out_i in range(n_out):
        t_target = out_i / FPS
        while (data_i + 1 < len(frames) and
               abs(frames[data_i + 1]['t'] - t_target) <=
               abs(frames[data_i]['t']     - t_target)):
            data_i += 1
        vw.write(_render_skel(frames[data_i]['lms'], floor_y, W, H))

    vw.release()

    @after_this_request
    def _cleanup(response):
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return response

    return send_file(
        tmp_path,
        as_attachment=True,
        download_name=f'{project}_{video_id}_skeleton.mp4',
        mimetype='video/mp4',
    )

def on_terminate(proc):
    print(f'Process {proc} terminated')

# ── Launch ─────────────────────────────────────────────────────────────────────
def _free_port(port: int):
    """
    Kill any process bound to *port*, then wait until the socket is actually
    released before returning.  On MacOS, poll untill connection is refused 
    (up to 3 s).
    """
    import psutil, socket, time

    # Find any occupying processes
    procs = []
    known_pids = set()
    for conn in psutil.net_connections(kind='inet'):
        # Ignore addresses and pids named "None"
        if conn.laddr and conn.pid and conn.laddr.port == port:
            if conn.pid in known_pids:
                continue
            known_pids.add(conn.pid)
            try:
                procs.append(psutil.Process(conn.pid))
            except psutil.NoSuchProcess:
                pass

    if not procs:
        return

    for p in procs:
        children = psutil.Process().children(recursive=True)
        for c in children:
            try:
                c.terminate()
            except psutil.NoSuchProcess:
                pass
        gone, alive = psutil.wait_procs(procs, timeout=1, callback=on_terminate)
        for c in alive:
            c.kill()

        p.terminate()
    gone, alive = psutil.wait_procs(procs, timeout=1, callback=on_terminate)
    for p in alive:
        p.kill()

    # Poll until the port is actually free (macOS can hold sockets briefly)
    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                pass           # still bound — keep waiting
        except (ConnectionRefusedError, OSError):
            return             # port is free
        time.sleep(0.1)


if __name__ == '__main__':
    load_dotenv()
    PORT = os.getenv("PP_PORT", 5050)

    if '--_server-mode' in sys.argv:
        # Background child — just run Flask
        app.run(host='127.0.0.1', port=PORT, debug=False)
    else:
        # Launcher: free port, spawn detached child, open browser, exit
        _free_port(PORT)
        log_path = os.path.join(_HERE, 'server.log')
        with open(log_path, 'w') as _log:
            child = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), '--_server-mode'],
                stdout=_log,
                stderr=_log,
                start_new_session=True,
            )
        print(f'PlatformPose running at http://localhost:{PORT}  (PID {child.pid})')
        print(f'Logs: {log_path}')
        time.sleep(0.9)
        webbrowser.open(f'http://localhost:{PORT}')
