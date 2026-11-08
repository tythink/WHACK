"""Automated verification suite for WHACK Role 1+2 Signals + Facial Fusion & Active Brain Caller."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict

SIGNALS_DIR = Path(__file__).resolve().parent
FACE_DIR = SIGNALS_DIR.parent / "face"
for _p in (str(SIGNALS_DIR), str(FACE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
from brain_caller import ActiveBrainCaller, LATEST_BRAIN_CALL_PATH
from input_caret_tracker import InputCaretTracker, get_screen_size_px
from prototype_signals_gui import CombinedSignalsFaceGUI
from status_fusion import MultimodalStatusFusionEngine, SIX_EMOTION_KEYS, redact_secrets


@dataclass
class _MockGaze:
    screen_x_px: int = 690
    screen_y_px: int = 415
    zone: str = "screen_editor"
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    confidence: float = 0.92


@dataclass
class _MockEmotion:
    primary_emotion: str = "flow"
    emotions: Dict[str, float] = None
    talking: bool = False
    confidence: float = 0.90

    def __post_init__(self) -> None:
        if self.emotions is None:
            self.emotions = {"flow": 0.85, "confused": 0.08, "frustrated": 0.05, "fatigued": 0.05, "delighted": 0.04, "surprised": 0.03}


@dataclass
class _MockBody:
    primary_gesture: str = "none"


def run_tests() -> None:
    print("1. Verifying live Win32 OS Mouse + Keyboard Caret (`|`) polling...")
    sw, sh = get_screen_size_px()
    assert sw > 0 and sh > 0
    trk = InputCaretTracker()
    live_snap = trk.poll()
    assert isinstance(live_snap.mouse_x_px, int) and isinstance(live_snap.caret_x_px, int)
    sb_snap = trk.poll(sandbox_caret=(680, 410))
    assert sb_snap.caret_detected and (sb_snap.caret_x_px, sb_snap.caret_y_px) == (680, 410)
    assert sb_snap.caret_source == "sandbox_editor"

    # Verify mouse frustration wiggle & multi-episode same-line caret erase oscillation
    t0 = 100.0
    for i, x in enumerate((500, 560, 490, 570, 480, 565, 485, 572, 482)):
        is_erase = i % 2 == 1
        snap_wig = trk.update_raw(
            now=t0 + i * 0.12,
            mouse_xy=(x, 500),
            caret_xy=(680 + (24 if not is_erase else -24), 410),
            erase_or_undo_edge=is_erase,
            erase_kind="undo" if is_erase else None,
            any_key_edge=not is_erase,
        )
    assert snap_wig.mouse_wiggle_score >= 0.60, f"Expected wiggle >= 0.60, got {snap_wig.mouse_wiggle_score}"
    assert snap_wig.caret_line_thrash_score >= 0.55, f"Expected caret thrash >= 0.55, got {snap_wig.caret_line_thrash_score}"
    assert snap_wig.erase_episodes >= 3
    assert "[REDACTED]" in redact_secrets("OPENAI_API_KEY=sk-1234567890abcdef")
    print("   OK: Win32 mouse, caret |, dwell, mouse wiggle, caret line thrash, and secret redaction verified.")

    print("1b. Verifying Typo vs Stuck Rewrite Thrashing threshold...")
    # Case 1: 10 rapid Backspaces fixing a typo in <1.5s + forward typing -> ONLY 1 episode, NEVER stuck!
    it_typo = InputCaretTracker()
    eng_typo = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=0.5)
    t_ty = 10.0
    it_typo.update_raw(now=t_ty, mouse_xy=(900, 500), caret_xy=(760, 410), any_key_edge=True)
    for k in range(10):
        t_ty += 0.12
        s_typo = it_typo.update_raw(
            now=t_ty,
            mouse_xy=(900, 500),
            caret_xy=(760 - (k + 1) * 8, 410),
            erase_or_undo_edge=True,
            erase_kind="backspace",
        )
    assert s_typo.undo_erase_burst == 10 and s_typo.erase_episodes == 1, (
        f"10 rapid Backspaces must collapse into 1 episode, got {s_typo.erase_episodes}"
    )
    res_typo = eng_typo.evaluate(
        s_typo,
        _MockGaze(690, 412),
        _MockEmotion("confused", {"confused": 0.68, "frustrated": 0.25, "flow": 0.20}),
        _MockBody(),
        face_detected=True,
        now=t_ty,
    )
    assert res_typo.status != "stuck_erase_thrash" and not res_typo.should_call_brain

    # Case 2: Tier A Multimodal Thrash (3 separate hesitant rewrite/Ctrl+Z episodes on same line + confused)
    it_thrash = InputCaretTracker()
    eng_thrash = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=0.5)
    t_th = 20.0
    it_thrash.update_raw(now=t_th, mouse_xy=(900, 500), caret_xy=(640, 410), any_key_edge=True)
    for ep_idx in range(3):
        t_th += 1.0  # Type forward on line y=410
        it_thrash.update_raw(now=t_th, mouse_xy=(900, 500), caret_xy=(730, 410), any_key_edge=True)
        t_th += 2.2  # Cognitive hesitation pause staring at line before deleting!
        s_th = it_thrash.update_raw(
            now=t_th,
            mouse_xy=(900, 500),
            caret_xy=(650, 410),
            erase_or_undo_edge=True,
            erase_kind="undo" if ep_idx == 1 else "backspace",
        )
    assert s_th.erase_episodes == 3 and s_th.hesitation_erases >= 2
    # Neutral poker-face at 3 episodes should NOT trigger yet (Tier B requires >= 5)
    res_poker_3 = eng_thrash.evaluate(s_th, _MockGaze(670, 412), _MockEmotion("flow"), _MockBody(), face_detected=True, now=t_th)
    assert res_poker_3.status != "stuck_erase_thrash"
    # With confused facial affect, Tier A triggers `stuck_erase_thrash`!
    res_tier_a = eng_thrash.evaluate(
        s_th,
        _MockGaze(670, 412),
        _MockEmotion("confused", {"confused": 0.66, "frustrated": 0.25, "flow": 0.15}),
        _MockBody("chin_rest"),
        face_detected=True,
        now=t_th + 0.6,
    )
    assert res_tier_a.status == "stuck_erase_thrash"

    # Case 3: Tier B Poker-Face Fallback (5 separate episodes trapped >=16s on same line even with neutral face)
    for _ in range(2):
        t_th += 2.5
        it_thrash.update_raw(now=t_th, mouse_xy=(900, 500), caret_xy=(735, 410), any_key_edge=True)
        t_th += 2.8
        s_th5 = it_thrash.update_raw(
            now=t_th,
            mouse_xy=(900, 500),
            caret_xy=(648, 410),
            erase_or_undo_edge=True,
            erase_kind="undo",
        )
    res_tier_b = eng_thrash.evaluate(s_th5, _MockGaze(670, 412), _MockEmotion("flow"), _MockBody(), face_detected=True, now=t_th)
    assert res_tier_b.status == "stuck_erase_thrash"
    print("   OK: 10-key rapid typo ignored (1 ep), Tier A (3 eps + confused) & Tier B (5 eps poker-face) verified.")

    print("2. Verifying Combined Signal + Facial User Status disambiguation...")
    # Scenario A: Focused Flow (moving caret + calm face -> NO Brain call)
    eng = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=1.2)
    it = InputCaretTracker()
    s_flow = it.update_raw(now=10.0, mouse_xy=(900, 500), caret_xy=(600, 400), any_key_edge=True)
    res_flow = eng.evaluate(s_flow, _MockGaze(610, 405), _MockEmotion("flow"), _MockBody(), face_detected=True, now=10.0)
    assert res_flow.status == "focused_flow" and not res_flow.should_call_brain

    # Scenario B1: Anti-False-AFK When Confused!
    # Squinting / furrowing brows / tucking chin on a laptop can push raw gaze `zone="looking_away"`
    # or `head_pitch=-36°`, or briefly occlude face (`<3s`) during head-scratch. Must NEVER say AFK!
    eng_conf = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=1.2)
    it_conf = InputCaretTracker()
    s_conf_early = it_conf.update_raw(now=0.0, mouse_xy=(900, 500), caret_xy=(680, 410), any_key_edge=True)
    s_conf_early = it_conf.update_raw(now=2.0, mouse_xy=(900, 500), caret_xy=(680, 410))
    res_conf_early = eng_conf.evaluate(
        s_conf_early,
        _MockGaze(685, 1080, zone="looking_away", head_yaw=6.0, head_pitch=-36.0),
        _MockEmotion("confused", {"confused": 0.78, "frustrated": 0.22, "flow": 0.10}),
        _MockBody("head_scratch"),
        face_detected=True,
        now=2.0,
    )
    assert res_conf_early.status == "confused_thinking", f"Expected confused_thinking, got {res_conf_early.status}"

    s_conf_dwell = it_conf.update_raw(now=16.0, mouse_xy=(900, 500), caret_xy=(680, 410))
    res_conf_dwell = eng_conf.evaluate(
        s_conf_dwell,
        _MockGaze(685, 1080, zone="looking_away", head_yaw=8.0, head_pitch=-36.0),
        _MockEmotion("confused", {"confused": 0.80, "frustrated": 0.20, "flow": 0.10}),
        _MockBody("head_scratch"),
        face_detected=True,
        now=16.0,
    )
    assert res_conf_dwell.status == "stuck_frozen_at_caret", (
        f"Confused user facing screen must NEVER be AFK! Got {res_conf_dwell.status}"
    )

    # Brief (<3s) hand occlusion during facepalm/head-scratch also must NOT flip to AFK
    res_conf_occ = eng_conf.evaluate(
        s_conf_dwell,
        None,
        None,
        None,
        face_detected=False,
        now=17.6,
    )
    assert res_conf_occ.status == "stuck_frozen_at_caret", (
        f"Brief <3s hand occlusion while confused must NOT trigger AFK! Got {res_conf_occ.status}"
    )

    # Scenario B2: True AFK Guard (left camera >=3s OR head turned >=45 deg sideways >=4s without confusion)
    # -> Status is `afk_away` AND main Brain is NEVER called!
    eng_afk = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=1.0)
    it_afk = InputCaretTracker()
    it_afk.update_raw(now=0.0, mouse_xy=(900, 500), caret_xy=(680, 410), any_key_edge=True)
    eng_afk.evaluate(
        it_afk.update_raw(now=10.0, mouse_xy=(900, 500), caret_xy=(680, 410)),
        None,
        None,
        None,
        face_detected=False,
        now=10.0,
    )
    s_afk = it_afk.update_raw(now=14.0, mouse_xy=(900, 500), caret_xy=(680, 410))
    res_afk_noface = eng_afk.evaluate(s_afk, None, None, None, face_detected=False, now=14.0)
    assert res_afk_noface.status == "afk_away" and not res_afk_noface.should_call_brain

    eng_afk_turn = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=1.0)
    eng_afk_turn.evaluate(
        s_afk,
        _MockGaze(10, 400, zone="looking_away", head_yaw=54.0, head_pitch=5.0),
        _MockEmotion("flow"),
        _MockBody(),
        face_detected=True,
        now=14.0,
    )
    s_afk2 = it_afk.update_raw(now=18.5, mouse_xy=(900, 500), caret_xy=(680, 410))
    res_afk_turn = eng_afk_turn.evaluate(
        s_afk2,
        _MockGaze(10, 400, zone="looking_away", head_yaw=54.0, head_pitch=5.0),
        _MockEmotion("flow"),
        _MockBody(),
        face_detected=True,
        now=18.5,
    )
    assert res_afk_turn.status == "afk_away" and not res_afk_turn.should_call_brain

    # Scenario C: Calm Reading Guard (mouse & caret idle 14s + calm `flow` + scanning gaze -> NO Brain call!)
    eng_read = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=1.2)
    it_read = InputCaretTracker()
    it_read.update_raw(now=0.0, mouse_xy=(1100, 600), caret_xy=(680, 410), any_key_edge=True)
    for idx, gx in enumerate((420, 580, 760, 910, 520)):
        s_read = it_read.update_raw(now=14.0 + idx * 0.2, mouse_xy=(1100, 600), caret_xy=(680, 410))
        res_read = eng_read.evaluate(s_read, _MockGaze(gx, 320), _MockEmotion("flow"), _MockBody(), face_detected=True, now=14.0 + idx * 0.2)
    assert res_read.status == "calm_reading" and not res_read.should_call_brain

    # Scenario D: Talking Guard (confused brow while talking -> suppressed, NO Brain call!)
    res_talk = eng_read.evaluate(
        s_read,
        _MockGaze(685, 412),
        _MockEmotion("confused", {"confused": 0.85, "frustrated": 0.30, "flow": 0.10}, talking=True),
        _MockBody("head_scratch"),
        face_detected=True,
        now=16.0,
    )
    assert res_talk.status == "talking_suppressed" and not res_talk.should_call_brain
    print("   OK: Flow, AFK, Calm Reading, and Talking anti-annoyance guards verified.")

    print("3. Verifying Active Role 3 Brain Caller, 4 Mandatory Inputs (3 Coords + 6 Emotions), & AFK Block...")
    received_calls = []

    def mock_role3_brain_receiver(mouse_xy, keyboard_xy, eye_gaze_xy, emotions, payload):
        received_calls.append((mouse_xy, keyboard_xy, eye_gaze_xy, emotions, payload))
        return {"summary": "Off-by-one slice on line 6", "quip": "Fenceposts strike again."}

    caller = ActiveBrainCaller(http_enabled=False, console_log=False)
    caller.register_callback(mock_role3_brain_receiver)

    # Hard AFK Guard: Even direct `invoke_brain` or `maybe_invoke` MUST NOT call the main Brain when AFK!
    assert caller.maybe_invoke(res_afk_noface, s_afk, eng_afk, now=14.0) is None
    assert caller.invoke_brain(res_afk_noface, s_afk, eng_afk, now=14.0) is None
    assert caller.invoke_brain(res_afk_turn, s_afk2, eng_afk_turn, now=18.5) is None
    assert caller.call_count == 0 and len(received_calls) == 0

    # Scenario E: Stuck Frozen at Caret (`|`) -> Actively calls Role 3 Brain with 3 coordinates + 6 emotions!
    eng_stuck = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=1.0)
    it_stuck = InputCaretTracker()
    it_stuck.update_raw(now=0.0, mouse_xy=(1120, 640), caret_xy=(680, 410), any_key_edge=True)

    dispatched = None
    six_emo_input = {
        "confused": 0.82,
        "frustrated": 0.35,
        "surprised": 0.14,
        "flow": 0.10,
        "fatigued": 0.18,
        "delighted": 0.02,
    }
    for step_t in (14.0, 14.6, 15.3):
        s_st = it_stuck.update_raw(now=step_t, mouse_xy=(1120, 640), caret_xy=(680, 410))
        st_res = eng_stuck.evaluate(
            s_st,
            _MockGaze(692, 418, zone="screen_editor"),
            _MockEmotion("confused", six_emo_input),
            _MockBody("head_scratch"),
            face_detected=True,
            now=step_t,
        )
        assert st_res.status == "stuck_frozen_at_caret"
        rec = caller.maybe_invoke(st_res, s_st, eng_stuck, now=step_t)
        if rec is not None:
            dispatched = rec

    assert dispatched is not None and len(received_calls) == 1
    m_xy, k_xy, g_xy, emos, pl = received_calls[0]
    assert m_xy == (1120, 640)
    assert k_xy == (680, 410)
    assert g_xy == (692, 418)
    assert tuple(emos.keys()) == SIX_EMOTION_KEYS
    assert emos["confused"] == 0.82 and emos["frustrated"] == 0.35 and emos["surprised"] == 0.14
    assert pl["mouse_cursor"]["x"] == 1120 and pl["mouse_cursor"]["y"] == 640
    assert pl["keyboard_cursor"]["x"] == 680 and pl["keyboard_cursor"]["y"] == 410 and pl["keyboard_cursor"]["detected"] is True
    assert pl["eye_gaze"]["x"] == 692 and pl["eye_gaze"]["y"] == 418
    assert set(pl["emotions"].keys()) == set(SIX_EMOTION_KEYS)
    assert pl["emotions"]["confused"] == 0.82 and pl["emotions"]["fatigued"] == 0.18
    assert pl["coordinates"]["mouse_cursor"] == {"x": 1120, "y": 640}
    assert pl["coordinates"]["keyboard_cursor"]["x"] == 680 and pl["coordinates"]["keyboard_cursor"]["y"] == 410
    assert pl["coordinates"]["eye_gaze"]["x"] == 692 and pl["coordinates"]["eye_gaze"]["y"] == 418
    assert pl["user_status"]["state"] == "stuck_frozen_at_caret"
    assert LATEST_BRAIN_CALL_PATH.exists()
    saved_outbox = json.loads(LATEST_BRAIN_CALL_PATH.read_text(encoding="utf-8"))
    assert saved_outbox["coordinates"]["mouse_cursor"] == {"x": 1120, "y": 640}
    assert set(saved_outbox["emotions"].keys()) == set(SIX_EMOTION_KEYS)

    # Verify default Placeholder Brain (`call_teammate_brain`) + 1-arg dict callback work before Role 3 is ready
    stub_caller = ActiveBrainCaller(http_enabled=False, console_log=False)
    stub_rec = stub_caller.invoke_brain(st_res, s_st)
    assert stub_rec is not None and "[Placeholder Brain]" in stub_rec.response_summary
    one_arg_packets = []
    stub_caller.register_callback(lambda pkt: (one_arg_packets.append(pkt), {"summary": "1-arg dict OK"})[1])
    stub_rec2 = stub_caller.invoke_brain(st_res, s_st)
    assert stub_rec2 is not None and len(one_arg_packets) == 1
    assert set(one_arg_packets[0]["emotions"].keys()) == set(SIX_EMOTION_KEYS)
    stub_caller.close()

    # Cooldown prevents duplicate immediate call on next frame
    s_next = it_stuck.update_raw(now=15.8, mouse_xy=(1120, 640), caret_xy=(680, 410))
    st_next = eng_stuck.evaluate(
        s_next,
        _MockGaze(692, 418),
        _MockEmotion("confused", {"confused": 0.82, "frustrated": 0.35, "flow": 0.10}),
        _MockBody("head_scratch"),
        face_detected=True,
        now=15.8,
    )
    assert not st_next.should_call_brain and caller.maybe_invoke(st_next, s_next, eng_stuck, now=15.8) is None

    # Scenario F: Caret <-> Terminal Error Attention Split (`stuck_attention_split`)
    eng_split = MultimodalStatusFusionEngine(cooldown_sec=20.0, trigger_hold_sec=0.5)
    it_split = InputCaretTracker()
    it_split.update_raw(now=0.0, mouse_xy=(800, 500), caret_xy=(680, 320), any_key_edge=True)
    for i, gy in enumerate((330, 880, 325, 900, 330)):
        t_cur = 9.0 + i * 0.4
        s_sp = it_split.update_raw(now=t_cur, mouse_xy=(800, 500), caret_xy=(680, 320))
        res_sp = eng_split.evaluate(
            s_sp,
            _MockGaze(685, gy, zone="screen_terminal" if gy > 700 else "screen_editor"),
            _MockEmotion("confused", {"confused": 0.72, "frustrated": 0.30, "flow": 0.15}),
            _MockBody(),
            face_detected=True,
            now=t_cur,
        )
    assert res_sp.status == "stuck_attention_split"

    # Scenario G: Eureka resolution resets stuck score when test passes
    eng_stuck.record_terminal_run("pytest", exit_code=0, tail_output="3 passed", now=18.0)
    res_eu = eng_stuck.evaluate(
        s_next,
        _MockGaze(692, 418),
        _MockEmotion("delighted", {"delighted": 0.84, "flow": 0.30, "confused": 0.05, "frustrated": 0.02}),
        _MockBody("thumbs_up"),
        face_detected=True,
        now=18.1,
    )
    assert res_eu.status == "eureka_resolved" and res_eu.stuck_score == 0.0
    caller.close()
    print("   OK: Active Brain Caller, 3-coord payload, cooldown, attention split, and Eureka reset verified.")

    print("4. Verifying Unified Prototype GUI initialization & frame tick...")
    gui = CombinedSignalsFaceGUI(headless_test=True)
    gui.sandbox_text.focus_force()
    gui.root.update_idletasks()
    gui._tick()
    gui.force_brain_call()
    assert gui.brain_caller.call_count >= 1
    gui.shutdown()
    print("   OK: CombinedSignalsFaceGUI initialized, ticked, dispatched Brain call, and shut down cleanly.")
    print("\nALL SIGNALS + FACIAL FUSION + ACTIVE BRAIN CALLER SELF-TESTS PASSED.")


if __name__ == "__main__":
    run_tests()
