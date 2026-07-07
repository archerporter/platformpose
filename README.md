# PlatformPose

**Screen-capture pose estimation for video research.**

PlatformPose extracts body pose data from any video playing in a browser — without API access, video downloads, or platform permissions. It uses [MediaPipe Pose Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker) to detect 33 body landmarks from screenshots taken during playback, and stores the results in a portable SQLite database you can query, visualize, or export to CSV.

Designed for researchers studying movement in social media video, it works with any platform that runs in a browser: YouTube, TikTok, Instagram, Vimeo, or locally served files.

---

## Features

- **Browser-based interface** — configure projects, define capture regions, and monitor your corpus from a local web app
- **Floating control panel** — a minimal Tkinter window stays on top while you play back video; press Space to start and stop
- **Pose filtering** — frames with too many low-confidence keypoints are automatically discarded; set your own visibility thresholds
- **Floor estimation** — reference floor position computed from foot landmarks, using either a rolling maximum or percentile-based method
- **Skeleton visualizer** — review any captured video as an animated landmark skeleton; scrub through frames, adjust playback speed, inspect per-frame depth
- **Retention metrics** — per-video and per-session clean-frame counts and retention rates, displayed as color-coded indicators
- **CSV export** — full landmark data (x, y, z, visibility for all 33 landmarks) as a flat CSV, ready for downstream analysis
- **Project-based corpus** — multiple videos accumulate into a single SQLite file per project

---

## Requirements

- **macOS** (primary platform; Linux and Windows may work with adjustments — see [Platform Notes](#platform-notes))
- **Python 3.11 or later**
- `pose_landmarker_heavy.task` — MediaPipe model file (see [Download the Model](#download-the-model))
- **Screen Recording permission** granted to Terminal (or whichever app runs Python) in System Settings → Privacy & Security

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/archerporter/platformpose.git
cd platformpose
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

<details>
<summary>Pinned versions (for reproducibility)</summary>

```
Flask==3.1.3
mediapipe==0.10.35
numpy==2.4.6
pandas==3.0.3
PyAutoGUI==0.9.54
pygame-ce==2.5.7
opencv-contrib-python==4.13.0.92
psutil==7.2.2
```
</details>

### 4. Download the model

PlatformPose uses MediaPipe's [Pose Landmarker Heavy](https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task) model. Download it and place it in the project root:

```bash
curl -L -o pose_landmarker_heavy.task \
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task"
```

The file should sit at `platformpose/pose_landmarker_heavy.task`. It is excluded from version control by `.gitignore` due to its size (~25 MB).

---

## Quick Start

```bash
# Activate your environment
source venv/bin/activate

# Start PlatformPose
python flask_app/app.py
```

PlatformPose launches in the background and returns the terminal immediately. The URL and process ID are printed at startup; the browser opens automatically after about one second.

```
PlatformPose running at http://localhost:5050  (PID 12345)
Logs: flask_app/server.log
```

To stop the server, use the printed PID (`kill 12345`) or simply re-run the script — it kills any existing instance on the port before starting a new one. Flask output (request logs, errors) is written to `flask_app/server.log` rather than the terminal.

---

## Usage Guide

### Step 1 — Configure your project

In the **Settings** sidebar, enter:

| Field | Description |
|---|---|
| **Project name** | Groups videos into a single database (`<project>.db`) |
| **Video ID** | A unique identifier for this recording session (e.g., `tiktok_001`) |
| **Researcher** | Optional — stored as metadata with each frame |
| **Notes** | Optional — session notes stored alongside the data |

Settings auto-save when you change any field.

Settings can be loaded at startup from `flask_app/settings.json`.  See `flask_app/settings.json.example` for an example file listing the default settings.  Any settings not specified in `settings.json` will be assigned the default values.

See [Settings Reference](#settings-reference) for a complete list of options.

### Step 2 — Define the capture region

Click **Define Region**. After a 3-second countdown (giving you time to switch windows), a region selector appears as an overlay over your screen. Drag the highlighted rectangle to cover the video player precisely, then press **Enter** or **Space** to confirm.

The selected coordinates are saved and used for all subsequent captures in this session.

### Step 3 — Check capture conditions

Before recording, review the **Before You Capture** checklist. Pose estimation quality depends heavily on what the camera sees:

- Full body visible, stable camera, single dancer in frame
- Adequate lighting, no obstructions over the dancer
- Capture region covers only the video player (no browser chrome)

### Step 4 — Launch the control panel

Click **Launch Control Panel**. A small floating window appears with a large pose counter. Navigate to your video in another tab and begin playback, then:

- Press **Space** (or click **Start**) to begin capturing
- Press **Space** (or click **Stop**) when the video ends
- The session saves automatically and the panel closes

### Step 5 — Review and export

**Corpus Status** on the home page shows all captured videos for the current project with frame counts, duration, and retention rates. Click **View →** next to any video to open the skeleton visualizer, or click **Export CSV** to download the full corpus.

---

## Skeleton Visualizer

The visualizer (`/visualize`) plays back captured sessions as an animated skeleton. Use it to confirm captures are complete and the pose data looks correct before exporting.

**Controls:**
- Play/pause and loop toggle
- Scrubber for frame-level navigation
- Arrow keys step one frame at a time; Space plays/pauses
- Playback speed: ¼×, ½×, 1×, 2×

**Session Info panel:**
- Frame index and timestamp
- Clean frames / total attempted
- Retention rate and out-of-frame rate (color-coded)
- Per-frame depth spread (z range across body landmarks)

**Depth encoding toggle:** When enabled, line width and joint size encode hip-relative z-depth — thicker lines and larger dots are closer to the camera. This uses MediaPipe's normalized z-coordinate, which is relative to the hip plane, not a metric depth.

**Floor line:** A dashed line shows the estimated floor position, computed at the 99th percentile of the maximum foot-landmark y-coordinate across the session. This is recomputed from the landmark data each time the visualizer loads, so it improves automatically as the corpus grows.

---

## Data Format

Each captured video produces rows in a project-level SQLite database at `<project>.db` in the project root.

### Table: `frames`

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Autoincrement primary key |
| `video_id` | TEXT | Identifier assigned at capture time |
| `frame` | INTEGER | Per-session screenshot index (resets each capture run) |
| `timestamp` | REAL | Seconds since session start (resets each capture run) |
| `researcher` | TEXT | From settings |
| `project` | TEXT | Project name |
| `notes` | TEXT | From settings |
| `floor_y` | REAL | Estimated floor y-coordinate at time of capture |
| `captured_at` | TEXT | UTC datetime of row insertion |
| `lm0_x` … `lm32_vis` | REAL | 33 landmarks × 4 values each (see below) |

### Landmark columns

Each of the 33 MediaPipe landmarks produces four columns:

```
lm{i}_x    — horizontal position, 0–1 (left → right)
lm{i}_y    — vertical position, 0–1 (top → bottom)
lm{i}_z    — hip-relative depth (negative = toward camera)
lm{i}_vis  — visibility confidence, 0–1
```

Landmark indices follow MediaPipe's [Pose Landmarker topology](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker): 0 = nose, 11–12 = shoulders, 15–16 = wrists, 23–24 = hips, 27–32 = ankle/heel/toe landmarks.

### CSV export

**Export CSV** on the home page downloads all rows for the current project as a flat CSV file, named `<project>_corpus_<timestamp>.csv`. The CSV preserves the full SQLite schema including all landmark columns, suitable for import into R, pandas, or any analysis environment.

---

## Settings Reference

| Setting | Default | Description |
|---|---|---|
| `project` | `my_project` | Project name; determines which `.db` file receives new frames |
| `video_id` | `video_001` | Video identifier; must be unique within a project to avoid mixing sessions |
| `researcher` | _(empty)_ | Stored as metadata with each frame |
| `notes` | _(empty)_ | Free-text session notes |
| `min_visibility` | `0.5` | Landmarks with confidence below this threshold count as low-visibility |
| `max_out_of_range` | `5` | Maximum number of low-visibility landmarks permitted before a frame is discarded |
| `floor_method` | `Rolling Minimum` | **Rolling Minimum** tracks floor using a rolling maximum of foot-landmark y over a sliding window. **Foot Contact Inference** uses the 99th percentile across the full session. |
| `window_size` | `30` | Number of frames for the rolling window (Rolling Minimum method only) |

---

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| PP_PORT | 5050 | Port to which PlatformPose is bound |

---

## Project Structure

```
platformpose/
├── flask_app/
│   ├── app.py                  # Flask server
│   ├── control_panel.py        # Tkinter capture window (launched as subprocess)
│   ├── settings.json           # Persisted settings (auto-generated, git-ignored)
│   ├── static/
│   │   ├── style.css
│   │   └── favicon.*           # SVG, PNG, ICO
│   └── templates/
│       ├── base.html
│       ├── index.html
│       ├── visualize.html
│       ├── about.html
│       └── partials/
│           ├── corpus.html
│           ├── launch_status.html
│           └── region_status.html
├── region_selector.py          # Pygame region-drawing tool
├── requirements.txt
├── pose_landmarker_heavy.task  # MediaPipe model (not in repo — download separately)
└── <project>.db                # SQLite corpus files (generated at capture time, git-ignored)
```

---

## Platform Notes

PlatformPose was developed and tested on **macOS**. The following notes apply to other platforms:

**macOS**
- Grant **Screen Recording** permission to Terminal (or your IDE) in System Settings → Privacy & Security → Screen Recording before the first run. PyAutoGUI's screenshot function requires this.
- The app automatically brings the control panel window to the front using AppleScript.

**Linux**
- **X11** is supported. XDG Desktop Portal must be installed with one or more backends.  scrot must also be installed.
- **Wayland** is not currently supported. <!-- PlatformPose detects Wayland automatically and redirects the region selector and capture pipeline to XWayland. XWayland must be installed (it is included by default on most distributions). -->
- Screen Recording permissions are not required.
- The AppleScript window-focus step is silently skipped.

**Windows**
- PyAutoGUI works on Windows. The tkinter control panel requires no changes.
- Windows has been observed to run `svchost.exe`, an essential system process, on port 5050.  You may set an alternate port for PlatformPose to use in `.env`.  If using in conjunction with [Figure and Frame](https://github.com/archerporter/figure-frame), be sure to set `PP_URL` and `FF_PORT` accordingly in that program's `.env` file.
---

## Companion Tool: Figure and Frame

[**Figure and Frame**](https://github.com/archerporter/figure-frame) is a separate analysis application that reads the same `.db` files PlatformPose produces and computes movement metrics oriented around the portrait-frame compensation hypothesis:

- **Distal/proximal velocity ratio** — how much faster peripheral joints (wrists, ankles) move relative to the body core (shoulders, hips)
- **X-axis direction change rate** — how frequently the dancer reverses horizontal direction per second
- **Centre-of-mass level change rate** — frequency of rising and falling

See the [Figure and Frame repository](https://github.com/archerporter/figure-frame) for installation and usage instructions.

---

## Research Context

PlatformPose was developed in response to the contraction of social media data access for academic researchers — what has been termed the *post-API research landscape*. Commercial platforms have progressively restricted or eliminated research-grade data access, creating methodological constraints for scholars working with digital cultural materials.

The tool operates at the screen level rather than the API level. It captures what is visible during normal browser playback, making it platform-agnostic and independent of platform data policies. This approach trades completeness (no audio, no metadata, no comments) for accessibility: any video a researcher can watch, they can analyze.

The initial research application is the movement analysis of social media dance video — specifically, how dancers adapt choreography to the constraints of portrait-frame composition on short-form video platforms.

---

## Author

**L. Archer Porter**  
Researcher working at the intersection of digital humanities, dance studies, and social media studies.

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
