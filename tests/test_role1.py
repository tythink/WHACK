"""
Unit Tests for Role 1 (Face & Fusion)
Tests:
  - Emotion detector and mock generator conformance to EmotionEvent contract
  - EMA stress calculation and confidence clamping >= 0.5
  - Fusion engine weighting and trigger threshold (> 0.8)
  - Antigravity prompt formatting
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.contracts.events import EmotionEvent, ActionEvent, TakeoverTriggerEvent
from src.services.emotion.emotion_detector import EmotionDetector, MockEmotionGenerator
from src.orchestrator.fusion_engine import FusionEngine
from src.services.core_agent.prompts import build_antigravity_prompt


class TestEmotionService(unittest.TestCase):
    def test_mock_emotion_generator_contract(self):
        generator = MockEmotionGenerator()
        event = generator.get_next_event()

        self.assertIn("timestamp", event)
        self.assertIn("faceDetected", event)
        self.assertIn("dominantEmotion", event)
        self.assertIn("stressIndex", event)
        self.assertIn("confidence", event)
        self.assertIn("emotions", event)
        self.assertIn("fps", event)

        # Confidence clamped >= 0.5
        self.assertGreaterEqual(event["confidence"], 0.5)
        # Stress index bounded 0.0 to 1.0
        self.assertGreaterEqual(event["stressIndex"], 0.0)
        self.assertLessEqual(event["stressIndex"], 1.0)

    def test_ema_stress_calculation(self):
        detector = EmotionDetector(alpha=0.5, min_confidence=0.5)
        self.assertEqual(detector.current_ema_stress, 0.0)

        # Raw stress 1.0 -> EMA should become 0.5
        s1 = detector.update_ema_stress(1.0)
        self.assertAlmostEqual(s1, 0.5, places=2)

        # Raw stress 1.0 again -> EMA should become 0.75
        s2 = detector.update_ema_stress(1.0)
        self.assertAlmostEqual(s2, 0.75, places=2)


class TestFusionEngine(unittest.TestCase):
    def test_fusion_trigger(self):
        engine = FusionEngine(w1=0.5, w2=0.5, threshold=0.8)

        # Baseline calm
        engine.update_emotion({"stressIndex": 0.2, "dominantEmotion": "neutral"})
        engine.update_action({"panicScore": 0.2, "tabSwitchRatePerMin": 2.0})
        self.assertFalse(engine.get_current_event()["isTriggered"])
        self.assertAlmostEqual(engine.latest_fusion_score, 0.2, places=2)

        # Stress escalation only: 0.5 * 0.9 + 0.5 * 0.2 = 0.55 <= 0.8
        engine.update_emotion({"stressIndex": 0.9, "dominantEmotion": "stress"})
        self.assertFalse(engine.get_current_event()["isTriggered"])
        self.assertAlmostEqual(engine.latest_fusion_score, 0.55, places=2)

        # Both streams escalate: 0.5 * 0.9 + 0.5 * 0.85 = 0.875 > 0.8 -> Trigger!
        engine.update_action({"panicScore": 0.85, "tabSwitchRatePerMin": 28.0, "activeWindow": "VS Code"})
        event = engine.get_current_event()
        self.assertTrue(event["isTriggered"])
        self.assertAlmostEqual(event["fusionScore"], 0.875, places=2)
        self.assertEqual(event["status"], "takeover_triggered")

    def test_prompt_generation(self):
        prompt = build_antigravity_prompt(
            active_window="Terminal - cargo test",
            task_summary="debugging compiler error",
            stress_index=0.92,
            panic_score=0.88,
            fusion_score=0.90,
            threshold=0.80,
        )
        self.assertIn("Terminal - cargo test", prompt)
        self.assertIn("debugging compiler error", prompt)
        self.assertIn("Take control, inspect open window, and complete the blocking step.", prompt)


if __name__ == "__main__":
    unittest.main()
