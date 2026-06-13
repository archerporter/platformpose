"""
PlatformPose control panel — Flask/SQLite version.
Launched by flask_app/app.py as a subprocess:
    python flask_app/control_panel.py <settings.json>
"""
import sys
import os
import json
import time
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk
import numpy as np
import pyautogui
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
import pandas as pd

_HERE        = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_HERE)

READY, RECORDING, SAVING, DONE, ERROR = 'READY', 'RECORDING', 'SAVING', 'DONE', 'ERROR'

LOG_FILE = os.path.join(_HERE, 'control_panel.log')


def _log(msg: str):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}\n'
    try:
        with open(LOG_FILE, 'a') as fh:
            fh.write(line)
    except Exception:
        pass
    print(line, end='', file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print('Usage: control_panel.py <settings.json>', file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        settings = json.load(f)

    reg_left   = settings['left']
    reg_top    = settings['top']
    reg_width  = settings['width']
    reg_height = settings['height']
    min_vis    = settings.get('min_visibility', 0.5)
    max_oor    = settings.get('max_out_of_range', 5)
    project    = settings.get('project', 'project')
    video_id   = settings.get('video_id', 'video')

    _log(f'Starting control_panel — project={project!r} video_id={video_id!r} '
         f'region=({reg_left},{reg_top},{reg_width}×{reg_height}) '
         f'min_vis={min_vis} max_oor={max_oor}')

    # ── MediaPipe setup ───────────────────────────────────────────────────────
    task_path = os.path.join(PROJECT_ROOT, 'pose_landmarker_heavy.task')
    if not os.path.exists(task_path):
        sys.exit(f'ERROR: pose model not found at {task_path}')

    base_options = mp_python.BaseOptions(model_asset_path=task_path)
    lm_options = mp_vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = mp_vision.PoseLandmarker.create_from_options(lm_options)

    # ── SQLite setup ──────────────────────────────────────────────────────────
    db_file = os.path.join(PROJECT_ROOT, f'{project}.db')

    lm_cols_ddl = '\n'.join(
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
{lm_cols_ddl}
        _pad        INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_video_id ON frames (video_id);
    CREATE INDEX IF NOT EXISTS idx_project  ON frames (project);
    """
    with sqlite3.connect(db_file) as conn:
        conn.executescript(ddl)

    # ── Shared state ──────────────────────────────────────────────────────────
    state       = [READY]
    frames_data = []
    frame_count = [0]   # total screenshots attempted
    pose_count  = [0]   # poses accepted into frames_data
    fps_val     = [0.0]
    save_error  = [None]  # populated if save_results raises
    capturing   = threading.Event()
    quit_flag   = threading.Event()

    # ── Capture thread ────────────────────────────────────────────────────────
    def capture_loop():
        start_t = None
        fps_buf = []
        while not quit_flag.is_set():
            if not capturing.is_set():
                time.sleep(0.04)
                continue
            t0 = time.time()
            if start_t is None:
                start_t = t0

            try:
                shot = pyautogui.screenshot(
                    region=(reg_left, reg_top, reg_width, reg_height)
                )
                raw = np.array(shot)
                # pyautogui returns RGBA on macOS; ascontiguousarray ensures
                # the RGB slice is a proper contiguous array for MediaPipe
                frame_rgb = np.ascontiguousarray(raw[:, :, :3])
            except Exception as e:
                _log(f'Screenshot error on frame {frame_count[0]}: {e}')
                continue

            if frame_count[0] == 0:
                _log(f'First frame: shape={raw.shape} dtype={raw.dtype}')

            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            result = landmarker.detect(mp_img)

            if result.pose_landmarks:
                lms = result.pose_landmarks[0]
                low_vis = sum(1 for lm in lms if lm.visibility < min_vis)
                if low_vis <= max_oor:
                    row = {
                        'video_id':  video_id,
                        'frame':     frame_count[0],
                        'timestamp': round(t0 - start_t, 4),
                    }
                    for i, lm in enumerate(lms):
                        row[f'lm{i}_x']   = round(lm.x,          6)
                        row[f'lm{i}_y']   = round(lm.y,          6)
                        row[f'lm{i}_z']   = round(lm.z,          6)
                        row[f'lm{i}_vis'] = round(lm.visibility,  4)
                    frames_data.append(row)
                    pose_count[0] += 1

            frame_count[0] += 1
            fps_buf.append(time.time() - t0)
            if len(fps_buf) > 15:
                fps_buf.pop(0)
            avg = sum(fps_buf) / len(fps_buf)
            fps_val[0] = round(1.0 / avg, 1) if avg > 0 else 0.0

    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()

    # ── Save function (runs in thread) ────────────────────────────────────────
    def save_results():
        _log(f'save_results() called: {len(frames_data)} accepted poses, '
             f'{frame_count[0]} total frames, video_id={video_id!r}')
        try:
            if not frames_data:
                _log('No accepted poses — nothing to save. '
                     f'(total screenshots: {frame_count[0]})')
                root.after(0, _finish)
                return

            df = pd.DataFrame(frames_data)
            _log(f'DataFrame shape: {df.shape}, columns: {list(df.columns)[:8]}…')

            method      = settings.get('floor_method', 'Rolling Minimum')
            window_size = int(settings.get('window_size', 30))

            # Use all six foot landmarks (ankle, heel, toe) so the estimate
            # captures the actual contact surface, not just the ankle joint.
            # lm27/28 = ankles, lm29/30 = heels, lm31/32 = toe tips.
            foot_y = df[['lm27_y', 'lm28_y',
                          'lm29_y', 'lm30_y',
                          'lm31_y', 'lm32_y']].max(axis=1)

            if method == 'Foot Contact Inference':
                # 99th-percentile: robust to airborne frames while capturing
                # the true floor contact level across the full session.
                df['floor_y'] = foot_y.quantile(0.99)
            else:
                # Rolling maximum over the full foot extent.
                df['floor_y'] = foot_y.rolling(window_size, min_periods=1).max()

            df['researcher'] = settings.get('researcher', '')
            df['project']    = project
            df['notes']      = settings.get('notes', '')

            with sqlite3.connect(db_file) as conn:
                df.to_sql('frames', conn, if_exists='append', index=False)

            _log(f'Saved {len(df)} rows to {db_file}')
            root.after(0, _finish)

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            _log(f'SAVE ERROR: {exc}\n{tb}')
            save_error[0] = str(exc)
            root.after(0, _show_error)

    # ── UI callbacks ──────────────────────────────────────────────────────────
    def _start():
        capturing.set()
        state[0] = RECORDING
        _refresh_ui()

    def _stop():
        capturing.clear()
        state[0] = SAVING
        _refresh_ui()
        threading.Thread(target=save_results, daemon=True).start()

    def _finish():
        state[0] = DONE
        _refresh_ui()
        root.after(900, root.destroy)

    def _show_error():
        state[0] = ERROR
        _refresh_ui()

    def on_toggle():
        if state[0] == READY:
            _start()
        elif state[0] == RECORDING:
            _stop()

    def on_close():
        capturing.clear()
        quit_flag.set()
        landmarker.close()
        root.destroy()

    # ── Live counter update ───────────────────────────────────────────────────
    def _tick():
        if state[0] == RECORDING:
            count_var.set(str(pose_count[0]))
            total = frame_count[0]
            fps_str = f'{fps_val[0]} fps' if fps_val[0] > 0 else '—'
            fps_var.set(f'{fps_str}  ·  {total} frames')
        if not quit_flag.is_set():
            root.after(200, _tick)

    # ── Refresh button / status state ─────────────────────────────────────────
    STATUS_COLOR = {
        READY:     '#48c78e',
        RECORDING: '#f14668',
        SAVING:    '#ffb347',
        DONE:      '#48c78e',
        ERROR:     '#f14668',
    }
    STATUS_LABEL = {
        READY:     '● Ready',
        RECORDING: '● Recording',
        SAVING:    '● Saving…',
        DONE:      '● Done',
        ERROR:     '● Save error',
    }
    BTN_LABEL = {
        READY:     '▶  Start',
        RECORDING: '■  Stop',
        SAVING:    'Saving…',
        DONE:      'Done',
        ERROR:     'Error',
    }
    BTN_STYLE = {
        READY:     'Green.TButton',
        RECORDING: 'Red.TButton',
        SAVING:    'Amber.TButton',
        DONE:      'Green.TButton',
        ERROR:     'Red.TButton',
    }

    def _refresh_ui():
        s = state[0]
        status_lbl.config(text=STATUS_LABEL[s], fg=STATUS_COLOR[s])
        action_btn.configure(text=BTN_LABEL[s], style=BTN_STYLE[s])
        if s in (SAVING, DONE, ERROR):
            action_btn.state(['disabled'])
        else:
            action_btn.state(['!disabled'])
        if s == DONE:
            count_var.set(str(pose_count[0]))
            fps_var.set(f'saved  ·  {frame_count[0]} frames')
        elif s == ERROR:
            err_short = (save_error[0] or 'unknown error')[:60]
            fps_var.set(f'See control_panel.log')
            count_var.set('!')

    # ── Build window ──────────────────────────────────────────────────────────
    root = tk.Tk()
    root.title('PlatformPose')
    root.resizable(False, False)
    root.attributes('-topmost', True)
    root.geometry('+40+40')
    root.configure(bg='#f5f5f7')

    _style = ttk.Style(root)
    _style.theme_use('clam')
    _style.configure('Green.TButton',
        background='#48c78e', foreground='white',
        font=('Helvetica', 13, 'bold'),
        borderwidth=0, focuscolor='none', padding=(14, 7))
    _style.map('Green.TButton',
        background=[('active', '#3dbc84'), ('disabled', '#a8e6cf')],
        foreground=[('disabled', '#ffffff')])
    _style.configure('Red.TButton',
        background='#f14668', foreground='white',
        font=('Helvetica', 13, 'bold'),
        borderwidth=0, focuscolor='none', padding=(14, 7))
    _style.map('Red.TButton',
        background=[('active', '#e03558'), ('disabled', '#f8a8b4')],
        foreground=[('disabled', '#ffffff')])
    _style.configure('Amber.TButton',
        background='#ffb347', foreground='white',
        font=('Helvetica', 13, 'bold'),
        borderwidth=0, focuscolor='none', padding=(14, 7))
    _style.map('Amber.TButton',
        background=[('active', '#f0a030'), ('disabled', '#ffd89b')],
        foreground=[('disabled', '#ffffff')])

    FONT_TITLE  = ('Helvetica', 14, 'bold')
    FONT_META   = ('Helvetica', 10)
    FONT_STATUS = ('Helvetica', 12)
    FONT_COUNT  = ('Helvetica', 28, 'bold')
    FONT_FPS    = ('Helvetica', 10)

    PAD = 16

    # Title bar
    title_frame = tk.Frame(root, bg='#ffffff', pady=10)
    title_frame.pack(fill='x')
    tk.Label(title_frame, text='PlatformPose', font=FONT_TITLE,
             bg='#ffffff', fg='#1d1d1f').pack(side='left', padx=PAD)

    tk.Frame(root, height=1, bg='#d2d2d7').pack(fill='x')

    # Body
    body = tk.Frame(root, bg='#f5f5f7', padx=PAD, pady=12)
    body.pack(fill='both', expand=True)

    tk.Label(body, text=f'{project}  ·  {video_id}',
             font=FONT_META, bg='#f5f5f7', fg='#86868b').pack(anchor='w')

    tk.Frame(body, height=8, bg='#f5f5f7').pack()

    status_lbl = tk.Label(body, text='● Ready', font=FONT_STATUS,
                          bg='#f5f5f7', fg='#48c78e')
    status_lbl.pack(anchor='w')

    tk.Frame(body, height=6, bg='#f5f5f7').pack()

    count_var = tk.StringVar(value='0')
    tk.Label(body, textvariable=count_var, font=FONT_COUNT,
             bg='#f5f5f7', fg='#1d1d1f').pack()

    tk.Label(body, text='poses accepted', font=FONT_META,
             bg='#f5f5f7', fg='#86868b').pack()

    tk.Frame(body, height=4, bg='#f5f5f7').pack()

    fps_var = tk.StringVar(value='—')
    tk.Label(body, textvariable=fps_var, font=FONT_FPS,
             bg='#f5f5f7', fg='#86868b').pack()

    tk.Frame(body, height=10, bg='#f5f5f7').pack()

    action_btn = ttk.Button(
        body, text='▶  Start',
        style='Green.TButton',
        cursor='hand2',
        command=on_toggle,
    )
    action_btn.pack(pady=(0, 4))

    tk.Frame(body, height=6, bg='#f5f5f7').pack()
    tk.Frame(root, height=1, bg='#d2d2d7').pack(fill='x')
    tk.Label(root,
             text=f'{reg_width}×{reg_height} at ({reg_left}, {reg_top})  ·  space',
             font=('Helvetica', 9), bg='#f5f5f7', fg='#aeaeb2',
             pady=5).pack()

    root.bind('<space>', lambda _: on_toggle())
    root.bind('<Escape>', lambda _: on_close())
    root.protocol('WM_DELETE_WINDOW', on_close)

    root.after(200, _tick)
    root.mainloop()
    quit_flag.set()


if __name__ == '__main__':
    main()
