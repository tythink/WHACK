"""
WHACK - Shared Event Contracts (Python dataclasses)
Reflects src/contracts/events.ts
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, Tuple, Literal
import time

DominantEmotion = Literal['neutral', 'calm', 'frustration', 'anger', 'stress', 'panic']
TakeoverStatus = Literal['monitoring', 'arming', 'takeover_triggered', 'executing', 'completed', 'cancelled']


@dataclass
class LandmarksInfo:
    faceFound: bool = True
    box: Optional[Tuple[float, float, float, float]] = None  # [x, y, w, h]


@dataclass
class EmotionEvent:
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    faceDetected: bool = True
    dominantEmotion: str = "neutral"
    stressIndex: float = 0.0  # 0.0 to 1.0 (EMA)
    confidence: float = 0.9  # Clamped >= 0.5
    emotions: Dict[str, float] = field(default_factory=lambda: {
        "neutral": 0.8,
        "frustration": 0.05,
        "anger": 0.05,
        "stress": 0.05,
        "panic": 0.05,
    })
    landmarks: Optional[Dict] = None
    fps: float = 30.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActionEvent:
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    tabSwitchRatePerMin: float = 0.0
    panicScore: float = 0.0  # 0.0 to 1.0
    clickBursts: int = 0
    jitterScore: float = 0.0  # 0.0 to 1.0
    activeWindow: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TakeoverTriggerEvent:
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    fusionScore: float = 0.0
    stressIndex: float = 0.0
    panicScore: float = 0.0
    weights: Dict[str, float] = field(default_factory=lambda: {"w1": 0.5, "w2": 0.5})
    threshold: float = 0.8
    isTriggered: bool = False
    status: str = "monitoring"
    agentSessionId: Optional[str] = None
    activeWindow: Optional[str] = None
    taskSummary: Optional[str] = None
    suggestedPrompt: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
