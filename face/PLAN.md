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

## 5. Fusion Engine Decision Matrix

| Face State | Gaze Zone | Editor/Terminal Signal | Fusion Action |
| :--- | :--- | :--- | :--- |
| `confused` or `frustrated` ($\ge 0.75$) | `screen_editor` or `screen_terminal` | Repeated error or stuck on test | **Trigger Proactive Nudge** (high confidence) |
| `confused` ($\ge 0.75$) | `looking_away` | No recent keystrokes | **Deep Thought Mode** (Suppress nudge / delay 15s) |
| Any emotion | Any zone | User is talking (`talking: true`) | **Suppress Nudge** (user communicating/pair programming) |
| `fatigued` ($\ge 0.80$) | `screen_editor` / slow gaze | Degraded typing cadence | **Break / Rest Recommendation** |
| `flow` ($\ge 0.80$) | `screen_editor` | Continuous typing | **Silence All Notifications** |

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
