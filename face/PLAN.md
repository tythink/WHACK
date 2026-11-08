# Role 1: Face & Fusion Architecture & Implementation Plan

## 1. Overview & Extended Scope
In addition to confusion detection, the `face/` pipeline incorporates:
1. **Multi-Emotion Analysis**: Real-time classification of developer affective states (confused, frustrated, surprised, neutral/flow, fatigued, eureka/delighted).
2. **Eye & Gaze Tracking**: MediaPipe iris landmark tracking to determine screen gaze vector, fixation duration, and off-screen / distraction detection.
3. **Decision Fusion Engine**: Correlating multi-emotion cues and gaze focus with editor and terminal telemetry.

---

## 2. Multi-Emotion Taxonomy

| Emotion State | Facial Landmark / Blendshape Correlates | Meaning in Developer Context |
| :--- | :--- | :--- |
| **`confused`** | Brow furrow (`browDownLeft`, `browDownRight`), slight eye squint | Struggling with logic, syntax error, unexpected test failure. |
| **`frustrated`** | Sustained brow lower + lip press / tight mouth + head tilt | Repeated failed attempts, stuck on bug for multiple iterations. |
| **`surprised`** | Brow raise (`browInnerUp`), wide eyes (`eyeWideLeft/Right`), jaw drop | Unexpected crash, sudden build break, surprising output. |
| **`flow` / `neutral`** | Relaxed facial muscles, steady gaze, baseline blink rate | Deep focus, productive coding flow. |
| **`fatigued`** | Prolonged blink duration (EAR < threshold), drooping eyelids, slow head roll | Late night coding, prolonged session fatigue, reduced attention. |
| **`eureka` / `delighted`** | Cheek raise (`cheekSquint`), smile (`mouthSmileLeft/Right`), relaxed brow | Bug solved, passing tests, successful deployment. |

---

## 3. Eye & Gaze Tracking Pipeline

### 3.1 Landmark Extraction
MediaPipe Face Mesh provides 478 3D landmarks, including refined iris points:
- **Left Iris**: Center `468`, perimeter `469-472`
- **Right Iris**: Center `473`, perimeter `474-477`
- **Eye Contours**: Outer/inner canthi, upper/lower eyelids

### 3.2 Key Metrics Computed
- **Eye Aspect Ratio (EAR)**:
  $$\text{EAR} = \frac{\|p_2 - p_6\| + \|p_3 - p_5\|}{2 \|p_1 - p_4\|}$$
  Detects blinks and prolonged eyelid closure (fatigue).
- **Gaze Vector (Iris-in-Eye Ratio)**:
  Computes horizontal and vertical offset of the iris center relative to the eye bounding box.
- **Combined Gaze & Head Pose**:
  Fuses iris gaze offset with 3D head pose (SolvePnP on 6 canonical facial points: nose tip, chin, eye corners, mouth corners).
- **Target Gaze Zones**:
  - `screen_editor`: Looking directly at code editor workspace.
  - `screen_terminal`: Looking at bottom/side terminal area.
  - `looking_away`: Looking off-screen / away from monitor.
  - `eyes_closed`: Blinking or micro-sleep.

---

## 4. Telemetry Event Contract (`/events`)

The face service emits events via HTTP POST to `http://localhost:3000/events`.

### Event Payload Schema
```json
{
  "source": "face",
  "timestamp": 1731000000000,
  "state": "confused",
  "score": 0.85,
  "confidence": 0.90,
  "emotions": {
    "confused": 0.85,
    "frustrated": 0.60,
    "surprised": 0.10,
    "flow": 0.15,
    "fatigued": 0.05,
    "delighted": 0.02
  },
  "gaze": {
    "zone": "screen_editor",
    "screen_x_px": 960,
    "screen_y_px": 540,
    "norm_x": 0.50,
    "norm_y": 0.50,
    "eyeball_pitch": -4.2,
    "eyeball_yaw": 1.1,
    "eye_origin_cm": [0.0, -3.2, -52.0],
    "fixation_ms": 3200,
    "iris_ratio_x": 0.51,
    "iris_ratio_y": 0.48,
    "eyes_closed": false,
    "blinks_per_min": 14.2,
    "confidence": 0.92
  },
  "head_pose": {
    "pitch": -2.1,
    "yaw": 1.4,
    "roll": 0.5
  },
  "body": {
    "primary_gesture": "head_scratch",
    "active_gestures": ["head_scratch", "lean_in"],
    "posture": "leaning_in",
    "lean_ratio": 1.22,
    "hands_detected": 1,
    "mouth_occluded": false
  },
  "talking": false
}
```

> **Backward Compatibility**: `state` and `score` are maintained at the top level for existing Role 4 Core Server stubs, while `emotions`, `gaze`, and `body` provide rich multimodal context for the Brain and Fusion engines.

---

## 4.5 Upper-Body Posture, Hand Gestures & Accuracy Boosters

1. **`face/body_gesture_tracker.py` (PoseLandmarker + HandLandmarker)**:
   - Correlates 21x2 3D hand landmarks and 33 upper-body landmarks with the 478 face landmarks via probabilistic Noisy-OR fusion:
     - `head_scratch`, `chin_rest`, `lean_in`, `head_tilt` $\rightarrow$ reinforces **`confused`**
     - `facepalm_or_rub`, `head_shake`, `hands_thrown_up`, `clenched_fist` $\rightarrow$ reinforces **`frustrated`**
     - `eye_rub`, `shoulder_slump` $\rightarrow$ reinforces **`fatigued`**
     - `thumbs_up`, `head_nod` $\rightarrow$ reinforces **`delighted`** (`eureka`)
2. **Hand-Occlusion Protection**:
   - Automatically freezes false `talking` detection and downweights distorted mouth/eye blendshapes whenever a hand rests on the chin, covers the mouth, or rubs an eye.
3. **Head-Invariant 3D Eyeball + Tilted-Screen Gaze Calibration (`9-Pt + Step 10 VOR Lock`) + CLAHE**:
   - Physics-constrained 3D ray-plane intersection ($\mathbf{P}_{\text{hit}} = \mathbf{O}_{\text{eye}} + \lambda \, R_{\text{head}} \mathbf{d}_{\text{eye}}$) with Vestibulo-Ocular Reflex (VOR) corneal-protrusion compensation (`face/eyeball_tracker.py`), 9-point Look-&-Click + 3.5s center Head-Move Lock (`G`), adaptive LAB-space CLAHE luminance normalization, blink-rate stress tracking, and post-distress `eureka` breakthrough memory.

---

## 5. Unified Signal + Facial User Status & Active Brain Caller Contract

### 5.1 Active Caller Architecture (Role 1+2 $\rightarrow$ Role 3 Brain)
Role 1+2 (`face/` + `signals/`) acts as the **active caller**, continuously fusing **Signal** inputs (mouse cursor, keyboard text cursor `|`, idle/dwell, undo/erase bursts, terminal/window events) with **Facial/Body/Gaze** inputs to classify the real-time **User Status**. **Role 3 (`brain/`) waits passively** until invoked by our fusion trigger (via direct Python callable/method or HTTP `POST http://localhost:5002/diagnose`).

Whenever an actionable stuck/intervention status triggers (and **never** when `afk_away`), we pass **4 mandatory inputs** (3 screen pixel coordinates + the 6 emotions):
1. **`mouse_cursor`**: `(mouse_x_px, mouse_y_px)` — current screen coordinate of the OS mouse pointer.
2. **`keyboard_cursor`**: `(caret_x_px, caret_y_px)` — current screen coordinate of the keyboard text cursor (vertical bar `|`).
3. **`eye_gaze`**: `(gaze_x_px, gaze_y_px)` — current 3D head-invariant eye-tracking coordinate on screen.
4. **`emotions`**: all **6 affective scores** (`confused`, `frustrated`, `surprised`, `flow`, `fatigued`, `delighted`).

```json
{
  "mouse_cursor": { "x": 1120, "y": 640 },
  "keyboard_cursor": { "x": 680, "y": 410, "detected": true },
  "eye_gaze": { "x": 692, "y": 418, "zone": "screen_editor" },
  "emotions": {
    "confused": 0.82,
    "frustrated": 0.35,
    "surprised": 0.05,
    "flow": 0.12,
    "fatigued": 0.06,
    "delighted": 0.02
  },
  "user_status": {
    "state": "stuck_frozen_at_caret",
    "score": 0.84,
    "confidence": 0.91,
    "primary_emotion": "confused",
    "body_gesture": "head_scratch",
    "reasons": [
      "keyboard_cursor idle 16.4s & mouse_cursor idle 18.1s",
      "eye_gaze pinned 15px from keyboard_cursor",
      "sustained confused (0.81) + head_scratch"
    ]
  },
  "hintLevel": "nudge",
  "attemptLog": "Last 5m: Caret frozen 16s at (680, 410); 4 backspaces/undos."
}
```

### 5.2 Combined Signal + Facial User Status Matrix

| Unified User Status | Signal Cues (`mouse_cursor`, `keyboard_cursor`, Dwell, Edits) | Facial & Gaze Cues (`eye_gaze`, Emotion, Body, Talking) | Action Toward Role 3 Brain |
| :--- | :--- | :--- | :--- |
| **`stuck_frozen_at_caret`** | `keyboard_cursor` & `mouse_cursor` idle $\ge 12\text{s}$ | `eye_gaze` pinned near `keyboard_cursor` + (`confused` / `frustrated` / `head_scratch` / `chin_rest`) | **Actively Call Brain** (pass 3 coordinates + status) |
| **`stuck_erase_thrash`** | Rapid `Backspace`/`Ctrl+Z` burst + `keyboard_cursor` oscillating on same line | On-screen `eye_gaze` + elevated `confused` / `frustrated` | **Actively Call Brain** (pass 3 coordinates + status) |
| **`stuck_attention_split`** | `keyboard_cursor` parked ($\ge 8\text{s}$) | `eye_gaze` / `mouse_cursor` darting $> 350\text{ px}$ between `keyboard_cursor` & error/docs + distress | **Actively Call Brain** (pass 3 coordinates + status) |
| **`frustrated_blocked`** | Mouse frustration wiggle **or** repeated terminal failure | `frustrated` / `surprised` + `facepalm_or_rub` / `hands_thrown_up` | **Actively Call Brain** (pass 3 coordinates + status) |
| **`afk_away`** | Mouse & keyboard cursor idle $> 15\text{s}$ | `looking_away` **or** no face detected | **Suppress & Pause Timers** (user away) |
| **`calm_reading`** | Mouse & keyboard cursor idle $> 10\text{s}$ | `flow` expression + `eye_gaze` scanning lines | **Suppress** (calmly reading code/docs) |
| **`talking_suppressed`** | Any | `talking: true` | **Suppress** (speaking/collaborating) |
| **`focused_flow`** | Steady `keyboard_cursor` progression (`dwell < 4s`) | `eye_gaze` near `keyboard_cursor` + `flow` | **Silence All** (Do-Not-Disturb) |
| **`fatigued`** | Degraded typing / long cursor idle | Sustained `fatigued` ($\ge 0.70$, lid droop / `eye_rub`) | **Break Recommendation** |
| **`eureka_resolved`** | Typing resumes / passing run | `delighted` / `thumbs_up` after prior distress | **Reset Stuck Score & Cooldown** |

---

## 6. Implementation Stages (inside `face/`)

1. **`face/mock_emitter.py`**:
   - Mock event generator including multiple emotions and gaze zone data.
   - Posts to `http://localhost:3000/events` every 10 seconds.
2. **`face/gaze_tracker.py`**:
   - Iris landmark tracking, EAR calculation, and gaze zone estimation.
3. **`face/emotion_classifier.py`**:
   - Baseline calibration (20s neutral face sampling).
   - Blendshape/mesh feature extraction (brow furrow, squint, lip press, brow raise).
   - Rolling window exponential smoothing.
4. **`face/pipeline.py`**:
   - Main runner capturing webcam, running gaze + emotion classification, emitting events with throttling (state change + 3s heartbeat).
5. **`face/fusion_engine.py`**:
   - Local decision fusion layer matching face, gaze, and editor signals.
