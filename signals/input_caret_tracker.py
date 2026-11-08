"""OS-wide Mouse Cursor, Keyboard Text Cursor (`|` Caret), Idle/Dwell & Thrash Tracker.

Tracks the two OS input coordinates required alongside `face/` 3D eye-gaze:
1. `mouse_cursor`: `(mouse_x_px, mouse_y_px)` + dwell time + frustration wiggle
2. `keyboard_cursor`: `(caret_x_px, caret_y_px)` (the vertical text bar `|`) + dwell time
   + Typo-safe Episode-based Rewrite/Undo thrash detection (collapses rapid Backspace bursts,
     tracks cognitive hesitation pauses before erasing, weights `Ctrl+Z`/`Ctrl+Y` higher,
     and checks whether the caret is trapped on the same line vs advancing forward).

Privacy guarantee: NEVER records alphanumeric keystrokes or typed text content.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple


@dataclass
class EraseEpisode:
    """Represents one contiguous erase/undo burst (e.g. 8 fast Backspaces = 1 episode)."""

    started_at: float
    last_at: float
    caret_y: int
    key_count: int = 1
    has_undo: bool = False
    has_redo: bool = False
    hesitation_before_sec: float = 0.0


@dataclass
class SignalSnapshot:
    """Per-frame snapshot of OS mouse cursor, keyboard text cursor (`|`), and behavioral dynamics."""

    timestamp_ms: int
    mouse_x_px: int
    mouse_y_px: int
    caret_x_px: int
    caret_y_px: int
    caret_detected: bool
    caret_source: str  # "sandbox_editor" | "win32_caret" | "msaa_caret" | "last_known" | "none"
    mouse_dwell_ms: int
    keyboard_dwell_ms: int
    caret_dwell_ms: int
    system_idle_ms: int
    mouse_velocity_px_s: float
    mouse_wiggle_score: float
    undo_erase_burst: int  # Raw erase/undo key count in rolling 30s
    erase_episodes: int  # Distinct rewrite/erase episodes on current line region (40s)
    weighted_erase_score: float  # Episode score weighted by hesitation, Ctrl+Z, and Undo<->Redo flips
    hesitation_erases: int  # Episodes preceded by a 1.2s..12s cognitive pause
    undo_redo_flips: int  # Ctrl+Z <-> Ctrl+Y indecision switches in 40s
    trapped_same_line_sec: float  # Seconds caret has stayed in same 1-2 line Y-band without net escape
    seconds_since_last_erase: float
    caret_line_thrash_score: float
    active_window_title: str
    window_switches_per_min: int


# ---------------------------------------------------------------------------
# Windows ctypes structures & bindings (safe no-op fallback on non-Windows)
# ---------------------------------------------------------------------------
_IS_WIN = sys.platform.startswith("win")

if _IS_WIN:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass
    try:
        _oleacc = ctypes.windll.oleacc
    except Exception:
        _oleacc = None

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("dwTime", wintypes.DWORD),
        ]

    class _GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _VARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.WORD),
            ("wReserved1", wintypes.WORD),
            ("wReserved2", wintypes.WORD),
            ("wReserved3", wintypes.WORD),
            ("llVal", ctypes.c_longlong),
            ("ullPad", ctypes.c_ulonglong),
        ]

    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL

    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    _user32.GetLastInputInfo.restype = wintypes.BOOL

    _kernel32.GetTickCount.argtypes = []
    _kernel32.GetTickCount.restype = wintypes.DWORD

    _user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)]
    _user32.GetGUIThreadInfo.restype = wintypes.BOOL

    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.ClientToScreen.restype = wintypes.BOOL

    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND

    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int

    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int

    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short

    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int

    _IID_IACCESSIBLE = _GUID(
        0x618736E0,
        0x3C3D,
        0x11CF,
        (ctypes.c_ubyte * 8)(0x81, 0x0C, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71),
    )
    _OBJID_CLIENT = ctypes.c_long(-4).value
    _OBJID_CARET = ctypes.c_long(-8).value
    _VT_I4 = 3
    _CHILDID_SELF = 0

    if _oleacc is not None:
        _oleacc.AccessibleObjectFromWindow.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _oleacc.AccessibleObjectFromWindow.restype = ctypes.c_long


_VK_BACK = 0x08
_VK_RETURN = 0x0D
_VK_CONTROL = 0x11
_VK_LEFT = 0x25
_VK_UP = 0x26
_VK_RIGHT = 0x27
_VK_DOWN = 0x28
_VK_DELETE = 0x2E
_VK_Z = 0x5A
_VK_Y = 0x59
_VK_LBUTTON = 0x01
_VK_RBUTTON = 0x02


def get_screen_size_px() -> Tuple[int, int]:
    """Return `(screen_width, screen_height)` in OS cursor pixel space."""
    if _IS_WIN:
        w = int(_user32.GetSystemMetrics(0))
        h = int(_user32.GetSystemMetrics(1))
        if w > 0 and h > 0:
            return w, h
    return 1920, 1080


def _query_msaa_caret(hwnd: int) -> Optional[Tuple[int, int]]:
    """Query `OBJID_CARET` via `oleacc.dll` (`IAccessible::accLocation`, vtable slot 22)."""
    if not _IS_WIN or _oleacc is None or not hwnd:
        return None
    ptr = ctypes.c_void_p()
    hr = _oleacc.AccessibleObjectFromWindow(
        wintypes.HWND(hwnd),
        wintypes.DWORD(_OBJID_CARET & 0xFFFFFFFF),
        ctypes.byref(_IID_IACCESSIBLE),
        ctypes.byref(ptr),
    )
    if hr != 0 or not ptr.value:
        return None

    try:
        vtable_ptr = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release_fn = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable_ptr[2])
        acc_location_fn = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long),
            _VARIANT,
        )(vtable_ptr[22])

        px = ctypes.c_long(0)
        py = ctypes.c_long(0)
        pw = ctypes.c_long(0)
        ph = ctypes.c_long(0)
        var_self = _VARIANT(vt=_VT_I4, llVal=_CHILDID_SELF)
        loc_hr = acc_location_fn(
            ptr, ctypes.byref(px), ctypes.byref(py), ctypes.byref(pw), ctypes.byref(ph), var_self
        )
        release_fn(ptr)
        if loc_hr == 0 and (px.value != 0 or py.value != 0 or ph.value > 0):
            cx = int(px.value + max(pw.value, 0) // 2)
            cy = int(py.value + max(ph.value, 14) // 2)
            if -4000 <= cx <= 12000 and -4000 <= cy <= 12000:
                return cx, cy
    except Exception:
        try:
            vtable_ptr = ctypes.cast(
                ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
            ).contents
            release_fn = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable_ptr[2])
            release_fn(ptr)
        except Exception:
            pass
    return None


def _nudge_accessibility_client(hwnd: int) -> None:
    """Query `OBJID_CLIENT` once on a newly focused window so Chromium/VS Code exposes caret."""
    if not _IS_WIN or _oleacc is None or not hwnd:
        return
    ptr = ctypes.c_void_p()
    hr = _oleacc.AccessibleObjectFromWindow(
        wintypes.HWND(hwnd),
        wintypes.DWORD(_OBJID_CLIENT & 0xFFFFFFFF),
        ctypes.byref(_IID_IACCESSIBLE),
        ctypes.byref(ptr),
    )
    if hr == 0 and ptr.value:
        try:
            vtable_ptr = ctypes.cast(
                ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
            ).contents
            release_fn = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable_ptr[2])
            release_fn(ptr)
        except Exception:
            pass


class InputCaretTracker:
    """Real-time tracker for `(mouse_x, mouse_y)`, `(caret_x, caret_y)`, dwell, and typo-safe thrash."""

    MOUSE_JITTER_DEADZONE_PX = 6.0
    CARET_JITTER_DEADZONE_PX = 4.0
    EPISODE_MERGE_GAP_SEC = 2.0  # Rapid Backspaces within <2.0s merge into 1 single episode
    EPISODE_WINDOW_SEC = 40.0  # Rolling window for distinct rewrite episodes
    SAME_LINE_BAND_PX = 28  # ~1-2 editor lines vertically

    def __init__(self) -> None:
        now = time.monotonic()
        sw, sh = get_screen_size_px()
        self._mouse_anchor: Tuple[int, int] = (sw // 2, sh // 2)
        self._last_mouse: Tuple[int, int] = (sw // 2, sh // 2)
        self._last_poll_at: float = now
        self._mouse_moved_at: float = now
        self._keyboard_active_at: float = now
        self._caret_moved_at: float = now

        self._caret_anchor: Tuple[int, int] = (sw // 2, sh // 2)
        self._last_known_caret: Optional[Tuple[int, int]] = None
        self._last_caret_seen_at: float = 0.0
        self._last_msaa_poll_at: float = 0.0
        self._cached_msaa_caret: Optional[Tuple[int, int]] = None
        self._woken_hwnds: set[int] = set()

        # Typo vs Stuck Rewrite Thrash state
        self._undo_timestamps: Deque[float] = deque(maxlen=160)
        self._episodes: Deque[EraseEpisode] = deque(maxlen=40)
        self._undo_redo_seq: Deque[Tuple[float, str]] = deque(maxlen=40)  # "undo" | "redo"
        self._typed_since_last_erase: bool = True
        self._last_forward_type_at: float = now
        self._last_erase_at: float = -1e9

        # Same-line band trap timer (resets when user moves cleanly to new lines + types forward)
        self._line_band_y: Optional[int] = None
        self._line_band_entered_at: float = now
        self._line_highwater_x: int = 0

        # Rolling histories for mouse wiggle, caret line oscillation & tab hops
        self._mouse_dx_hist: Deque[Tuple[float, float, bool]] = deque(maxlen=60)
        self._caret_trace: Deque[Tuple[float, int, int]] = deque(maxlen=90)
        self._window_switches: Deque[float] = deque(maxlen=80)
        self._last_window_title: str = ""
        self._prev_key_down: dict[str, bool] = {}
        self._prev_sys_idle_ms: int = 0

    def record_sandbox_edit_event(
        self,
        is_erase_or_undo: bool = False,
        edit_kind: Optional[str] = None,
    ) -> None:
        """Notify tracker when an edit occurs inside the GUI's built-in sandbox editor.

        `edit_kind`: `"backspace"` | `"undo"` | `"redo"` | `"newline"` | `"forward"`
        """
        now = time.monotonic()
        self._keyboard_active_at = now
        kind = edit_kind or ("backspace" if is_erase_or_undo else "forward")
        cy = self._caret_anchor[1]
        if kind in ("backspace", "undo", "redo"):
            self._ingest_erase_action(now=now, erase_kind=kind, caret_y=cy)
        elif kind == "newline":
            self._typed_since_last_erase = True
            self._last_forward_type_at = now
            # Moving onto a new line with Enter after typing forward clears same-line trap
            self._line_band_y = None
        else:
            self._typed_since_last_erase = True
            self._last_forward_type_at = now

    def _ingest_erase_action(self, now: float, erase_kind: str, caret_y: int) -> None:
        """Group rapid Backspaces into 1 episode; track hesitation pauses & Ctrl+Z/Ctrl+Y."""
        self._undo_timestamps.append(now)

        if erase_kind in ("undo", "redo"):
            self._undo_redo_seq.append((now, erase_kind))

        can_merge = (
            bool(self._episodes)
            and not self._typed_since_last_erase
            and (now - self._episodes[-1].last_at) <= self.EPISODE_MERGE_GAP_SEC
        )
        if can_merge:
            ep = self._episodes[-1]
            ep.last_at = now
            ep.key_count += 1
            if erase_kind == "undo":
                ep.has_undo = True
            elif erase_kind == "redo":
                ep.has_redo = True
        else:
            # New distinct rewrite/erase episode! Measure cognitive pause before deciding to erase:
            ref_t = max(self._last_forward_type_at, self._last_erase_at)
            pause_before = max(0.0, now - ref_t) if ref_t > 0 else 0.0
            self._episodes.append(
                EraseEpisode(
                    started_at=now,
                    last_at=now,
                    caret_y=caret_y,
                    key_count=1,
                    has_undo=(erase_kind == "undo"),
                    has_redo=(erase_kind == "redo"),
                    hesitation_before_sec=pause_before,
                )
            )

        self._typed_since_last_erase = False
        self._last_erase_at = now

    def _poll_win32_caret(self, fg_hwnd: int, now: float) -> Tuple[Optional[Tuple[int, int]], str]:
        """Try Win32 `GetGUIThreadInfo` first, then throttled MSAA `OBJID_CARET`."""
        if not _IS_WIN:
            return None, "none"

        tid = int(_user32.GetWindowThreadProcessId(wintypes.HWND(fg_hwnd), None)) if fg_hwnd else 0
        gui = _GUITHREADINFO()
        gui.cbSize = ctypes.sizeof(_GUITHREADINFO)
        focus_hwnd = fg_hwnd

        if _user32.GetGUIThreadInfo(wintypes.DWORD(tid), ctypes.byref(gui)):
            if gui.hwndFocus:
                focus_hwnd = int(gui.hwndFocus)
            if gui.hwndCaret:
                pt = wintypes.POINT(
                    int(gui.rcCaret.left),
                    int((gui.rcCaret.top + gui.rcCaret.bottom) // 2),
                )
                if _user32.ClientToScreen(gui.hwndCaret, ctypes.byref(pt)):
                    if -4000 <= pt.x <= 12000 and -4000 <= pt.y <= 12000:
                        return (int(pt.x), int(pt.y)), "win32_caret"

        if (now - self._last_msaa_poll_at) >= 0.08:
            self._last_msaa_poll_at = now
            if fg_hwnd and fg_hwnd not in self._woken_hwnds:
                if len(self._woken_hwnds) > 64:
                    self._woken_hwnds.clear()
                self._woken_hwnds.add(fg_hwnd)
                _nudge_accessibility_client(fg_hwnd)
            for candidate in (focus_hwnd, fg_hwnd):
                if candidate:
                    res = _query_msaa_caret(candidate)
                    if res is not None:
                        self._cached_msaa_caret = res
                        return res, "msaa_caret"
            self._cached_msaa_caret = None
        elif self._cached_msaa_caret is not None:
            return self._cached_msaa_caret, "msaa_caret"

        return None, "none"

    def poll(
        self,
        sandbox_caret: Optional[Tuple[int, int]] = None,
    ) -> SignalSnapshot:
        """Poll current OS mouse cursor, keyboard caret (`|`), and behavioral metrics."""
        now = time.monotonic()

        if not _IS_WIN:
            mx, my = self._last_mouse
            return self.update_raw(
                now=now,
                mouse_xy=(mx, my),
                caret_xy=sandbox_caret,
                caret_source="sandbox_editor" if sandbox_caret is not None else "none",
                system_idle_ms=int((now - min(self._mouse_moved_at, self._keyboard_active_at)) * 1000),
                erase_or_undo_edge=False,
                any_key_edge=False,
                mouse_clicked=False,
                window_title="Session",
            )

        # 1. OS Mouse Cursor `(mouse_x_px, mouse_y_px)`
        pt = wintypes.POINT()
        if _user32.GetCursorPos(ctypes.byref(pt)):
            mx, my = int(pt.x), int(pt.y)
        else:
            mx, my = self._last_mouse

        # 2. OS System Idle (`GetLastInputInfo`)
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if _user32.GetLastInputInfo(ctypes.byref(lii)):
            tick = int(_kernel32.GetTickCount()) & 0xFFFFFFFF
            sys_idle_ms = max(0, (tick - int(lii.dwTime)) & 0xFFFFFFFF)
        else:
            sys_idle_ms = 0

        # 3. Privacy-safe structural key edge checks (skip double-counting when sandbox editor already reported)
        def _is_down(vk: int) -> bool:
            return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

        ctrl_down = _is_down(_VK_CONTROL)
        structural_states = {
            "back": _is_down(_VK_BACK),
            "del": _is_down(_VK_DELETE),
            "undo": ctrl_down and _is_down(_VK_Z),
            "redo": ctrl_down and _is_down(_VK_Y),
            "ret": _is_down(_VK_RETURN),
            "nav": (
                _is_down(_VK_LEFT)
                or _is_down(_VK_RIGHT)
                or _is_down(_VK_UP)
                or _is_down(_VK_DOWN)
            ),
        }
        detected_erase_kind: Optional[str] = None
        struct_edge = False
        newline_edge = False
        for k, is_dn in structural_states.items():
            was_dn = self._prev_key_down.get(k, False)
            if is_dn and not was_dn:
                struct_edge = True
                if k == "undo":
                    detected_erase_kind = "undo"
                elif k == "redo":
                    detected_erase_kind = "redo"
                elif k in ("back", "del") and detected_erase_kind is None:
                    detected_erase_kind = "backspace"
                elif k == "ret":
                    newline_edge = True
            self._prev_key_down[k] = is_dn

        mouse_clicked = _is_down(_VK_LBUTTON) or _is_down(_VK_RBUTTON)
        mouse_step = math.hypot(mx - self._last_mouse[0], my - self._last_mouse[1])
        non_mouse_input_reset = (
            sys_idle_ms < 120
            and (sys_idle_ms + 80 < self._prev_sys_idle_ms or sys_idle_ms == 0)
            and mouse_step < 2.0
            and not mouse_clicked
        )
        self._prev_sys_idle_ms = sys_idle_ms

        # When `sandbox_caret` is active, `_on_sandbox_keypress` already classified the exact key
        if sandbox_caret is not None:
            detected_erase_kind = None
            newline_edge = False

        any_key_edge = struct_edge or non_mouse_input_reset

        # 4. Foreground Window Title
        fg_hwnd = int(_user32.GetForegroundWindow() or 0)
        win_title = ""
        if fg_hwnd:
            length = int(_user32.GetWindowTextLengthW(wintypes.HWND(fg_hwnd)))
            if 0 < length <= 260:
                buf = ctypes.create_unicode_buffer(length + 2)
                _user32.GetWindowTextW(wintypes.HWND(fg_hwnd), buf, length + 1)
                win_title = buf.value.strip()

        # 5. Keyboard Text Cursor (`|` Caret) `(caret_x_px, caret_y_px)`
        if sandbox_caret is not None:
            caret_xy: Optional[Tuple[int, int]] = sandbox_caret
            caret_source = "sandbox_editor"
        else:
            caret_xy, caret_source = self._poll_win32_caret(fg_hwnd, now)

        return self.update_raw(
            now=now,
            mouse_xy=(mx, my),
            caret_xy=caret_xy,
            caret_source=caret_source,
            system_idle_ms=sys_idle_ms,
            erase_or_undo_edge=(detected_erase_kind is not None),
            erase_kind=detected_erase_kind,
            newline_edge=newline_edge,
            any_key_edge=any_key_edge,
            mouse_clicked=mouse_clicked,
            window_title=win_title,
        )

    def update_raw(
        self,
        now: float,
        mouse_xy: Tuple[int, int],
        caret_xy: Optional[Tuple[int, int]],
        caret_source: str = "win32_caret",
        system_idle_ms: int = 0,
        erase_or_undo_edge: bool = False,
        erase_kind: Optional[str] = None,
        newline_edge: bool = False,
        any_key_edge: bool = False,
        mouse_clicked: bool = False,
        window_title: str = "",
    ) -> SignalSnapshot:
        """Core deterministic state update (used by both live `poll()` and unit tests)."""
        dt = max(1e-3, now - self._last_poll_at)
        self._last_poll_at = now

        mx, my = int(mouse_xy[0]), int(mouse_xy[1])
        inst_step = math.hypot(mx - self._last_mouse[0], my - self._last_mouse[1])
        dx = float(mx - self._last_mouse[0])
        self._last_mouse = (mx, my)

        # Mouse dwell with optical micro-jitter deadzone
        if (
            math.hypot(mx - self._mouse_anchor[0], my - self._mouse_anchor[1])
            > self.MOUSE_JITTER_DEADZONE_PX
            or mouse_clicked
        ):
            self._mouse_anchor = (mx, my)
            self._mouse_moved_at = now
        mouse_dwell_ms = int(max(0.0, (now - self._mouse_moved_at) * 1000.0))
        mouse_vel = inst_step / dt

        # Frustration mouse wiggle: rapid horizontal reversals in 1.35s without clicking
        self._mouse_dx_hist.append((now, dx, mouse_clicked))
        while self._mouse_dx_hist and (now - self._mouse_dx_hist[0][0]) > 1.35:
            self._mouse_dx_hist.popleft()

        recent_clicks = any(c for _, _, c in self._mouse_dx_hist)
        reversals = 0
        last_sign = 0
        total_abs_dx = 0.0
        for _, step_x, _ in self._mouse_dx_hist:
            if abs(step_x) >= 14.0:
                sgn = 1 if step_x > 0 else -1
                if last_sign != 0 and sgn != last_sign:
                    reversals += 1
                last_sign = sgn
                total_abs_dx += abs(step_x)
        if recent_clicks or reversals < 3:
            wiggle_score = 0.0
        else:
            wiggle_score = min(1.0, ((reversals - 2) / 4.0) * min(1.0, total_abs_dx / 420.0))

        # Caret `|` position, same-line trap timer & forward-progress detection
        prev_cx, prev_cy = self._caret_anchor
        if caret_xy is not None:
            cx, cy = int(caret_xy[0]), int(caret_xy[1])
            caret_detected = True
            resolved_source = caret_source
            self._last_known_caret = (cx, cy)
            self._last_caret_seen_at = now

            if math.hypot(cx - prev_cx, cy - prev_cy) > self.CARET_JITTER_DEADZONE_PX:
                self._caret_anchor = (cx, cy)
                self._caret_moved_at = now
                self._keyboard_active_at = now
                self._caret_trace.append((now, cx, cy))

                # Detect forward typing progression vs staying trapped on the same line
                if self._line_band_y is None or abs(cy - self._line_band_y) > self.SAME_LINE_BAND_PX:
                    self._line_band_y = cy
                    self._line_band_entered_at = now
                    self._line_highwater_x = cx
                    if cy > prev_cy + self.SAME_LINE_BAND_PX and not erase_or_undo_edge:
                        # Advancing downward onto a fresh line -> mark forward progress
                        self._typed_since_last_erase = True
                        self._last_forward_type_at = now
                else:
                    if cx > prev_cx + 4 and not erase_or_undo_edge and erase_kind is None:
                        self._typed_since_last_erase = True
                        self._last_forward_type_at = now
                    if cx > self._line_highwater_x + 28 and (now - self._last_erase_at) >= 2.0:
                        # Smoothly typed well past previous point on this line (typo fixed & moved on!)
                        self._line_highwater_x = cx
        elif self._last_known_caret is not None and (now - self._last_caret_seen_at) <= 60.0:
            cx, cy = self._last_known_caret
            caret_detected = True
            resolved_source = "last_known"
        else:
            cx, cy = self._caret_anchor
            caret_detected = False
            resolved_source = "none"

        resolved_erase_kind = erase_kind or ("backspace" if erase_or_undo_edge else None)
        if resolved_erase_kind is not None:
            self._keyboard_active_at = now
            self._ingest_erase_action(now=now, erase_kind=resolved_erase_kind, caret_y=cy)
        elif newline_edge:
            self._keyboard_active_at = now
            self._typed_since_last_erase = True
            self._last_forward_type_at = now
            self._line_band_y = cy
            self._line_band_entered_at = now
        elif any_key_edge:
            self._keyboard_active_at = now
            self._typed_since_last_erase = True
            self._last_forward_type_at = now

        # Prune rolling windows
        while self._undo_timestamps and (now - self._undo_timestamps[0]) > 30.0:
            self._undo_timestamps.popleft()
        while self._episodes and (now - self._episodes[0].last_at) > self.EPISODE_WINDOW_SEC:
            self._episodes.popleft()
        while self._undo_redo_seq and (now - self._undo_redo_seq[0][0]) > self.EPISODE_WINDOW_SEC:
            self._undo_redo_seq.popleft()
        while self._caret_trace and (now - self._caret_trace[0][0]) > 22.0:
            self._caret_trace.popleft()

        # Compute Episode-Based Rewrite Metrics (filtering to active line band when caret is known)
        if caret_detected:
            relevant_eps = [
                ep for ep in self._episodes if abs(ep.caret_y - cy) <= self.SAME_LINE_BAND_PX * 2
            ]
        else:
            relevant_eps = list(self._episodes)

        episode_count = len(relevant_eps)
        hesitation_cnt = sum(
            1 for ep in relevant_eps if 1.2 <= ep.hesitation_before_sec <= 15.0
        )

        # Count Ctrl+Z <-> Ctrl+Y flip-flops (undo -> redo or redo -> undo)
        undo_redo_flips = 0
        seq_list = list(self._undo_redo_seq)
        for i in range(1, len(seq_list)):
            if seq_list[i][1] != seq_list[i - 1][1]:
                undo_redo_flips += 1

        weighted_score = 0.0
        for ep in relevant_eps:
            if ep.has_undo or ep.has_redo:
                w = 1.50
            elif 1.2 <= ep.hesitation_before_sec <= 15.0:
                w = 1.15
            else:
                # Fast immediate Backspace burst (<1.2s pause) -> mild weight (likely typo)
                w = 0.70
            weighted_score += w
        weighted_score += 0.75 * undo_redo_flips

        # Dampen stale score if user fixed a typo and has been smoothly typing forward onto new text
        secs_since_erase = max(0.0, now - self._last_erase_at) if self._last_erase_at > 0 else 999.0
        if (
            self._typed_since_last_erase
            and secs_since_erase >= 3.0
            and (now - self._last_forward_type_at) <= 2.2
        ):
            weighted_score *= 0.45

        trapped_same_line_sec = (
            max(0.0, now - self._line_band_entered_at) if self._line_band_y is not None else 0.0
        )
        line_thrash = self._compute_caret_line_thrash(episode_count=episode_count)

        # Window switch rate (tab-hopping thrash)
        clean_title = window_title[:80]
        if clean_title and clean_title != self._last_window_title:
            if self._last_window_title:
                self._window_switches.append(now)
            self._last_window_title = clean_title
        while self._window_switches and (now - self._window_switches[0]) > 60.0:
            self._window_switches.popleft()

        keyboard_dwell_ms = int(max(0.0, (now - self._keyboard_active_at) * 1000.0))
        caret_dwell_ms = int(max(0.0, (now - self._caret_moved_at) * 1000.0))

        return SignalSnapshot(
            timestamp_ms=int(time.time() * 1000),
            mouse_x_px=mx,
            mouse_y_px=my,
            caret_x_px=cx,
            caret_y_px=cy,
            caret_detected=caret_detected,
            caret_source=resolved_source,
            mouse_dwell_ms=mouse_dwell_ms,
            keyboard_dwell_ms=keyboard_dwell_ms,
            caret_dwell_ms=caret_dwell_ms,
            system_idle_ms=int(system_idle_ms),
            mouse_velocity_px_s=round(mouse_vel, 1),
            mouse_wiggle_score=round(wiggle_score, 2),
            undo_erase_burst=len(self._undo_timestamps),
            erase_episodes=episode_count,
            weighted_erase_score=round(weighted_score, 2),
            hesitation_erases=hesitation_cnt,
            undo_redo_flips=undo_redo_flips,
            trapped_same_line_sec=round(trapped_same_line_sec, 1),
            seconds_since_last_erase=round(min(secs_since_erase, 999.0), 1),
            caret_line_thrash_score=round(line_thrash, 2),
            active_window_title=clean_title or "Desktop",
            window_switches_per_min=len(self._window_switches),
        )

    def _compute_caret_line_thrash(self, episode_count: int) -> float:
        """Detect repeated right-then-left rewrite cycles on a single line (ignores 1-off typos)."""
        if episode_count <= 1 and len(self._caret_trace) < 7:
            return 0.0
        pts = list(self._caret_trace)
        if len(pts) < 6:
            return 0.0
        latest_y = pts[-1][2]
        same_line_xs = [x for _, x, y in pts if abs(y - latest_y) <= self.SAME_LINE_BAND_PX]
        if len(same_line_xs) < 6:
            return 0.0

        # Count distinct forward->backward reversal cycles (require >= 3 cycles so 1 typo cycle = 0)
        dir_flips = 0
        backward_cycles = 0
        prev_dir = 0
        for i in range(1, len(same_line_xs)):
            dx = same_line_xs[i] - same_line_xs[i - 1]
            if abs(dx) < 6:
                continue
            cur_dir = 1 if dx > 0 else -1
            if prev_dir != 0 and cur_dir != prev_dir:
                dir_flips += 1
                if cur_dir < 0:
                    backward_cycles += 1
            prev_dir = cur_dir

        if backward_cycles < 2 or dir_flips < 4:
            return 0.0
        return min(1.0, 0.16 * dir_flips + 0.14 * backward_cycles)
