"""
WHACK - Role 1 (Face & Fusion) Runner
Launches:
  1. Stream B (Face / Emotion Detection) on port 5001
  2. Stream D (Fusion Orchestrator) on port 8080
"""

import sys
import os
import argparse
import subprocess
import time
import signal

PYTHON = sys.executable


def main():
    parser = argparse.ArgumentParser(description="Run WHACK Role 1 (Face & Fusion)")
    parser.add_argument("--mock", action="store_true", default=True, help="Run Emotion service in mock escalation mode (default: True)")
    parser.add_argument("--no-mock", action="store_false", dest="mock", help="Run Emotion service with real webcam")
    args = parser.parse_args()

    procs = []
    try:
        print("=" * 60)
        print("WHACK — Starting Role 1 (Face & Fusion Orchestrator)")
        print("=" * 60)

        # 1. Start Stream B (Emotion)
        emotion_cmd = [PYTHON, "src/services/emotion/server.py", "--port", "5001"]
        if args.mock:
            emotion_cmd.append("--mock")
        print(f"[*] Starting Stream B (Emotion) on :5001 (mock={args.mock})...")
        p_emotion = subprocess.Popen(emotion_cmd)
        procs.append(p_emotion)

        time.sleep(1.0)

        # 2. Start Stream D (Fusion Orchestrator)
        orchestrator_cmd = [PYTHON, "src/orchestrator/server.py", "--port", "8080", "--emotion-port", "5001", "--action-port", "5002"]
        print("[*] Starting Stream D (Fusion Orchestrator) on :8080...")
        p_orch = subprocess.Popen(orchestrator_cmd)
        procs.append(p_orch)

        print("\nAll Role 1 services are running:")
        print("  - Stream B (Emotion):      http://localhost:5001 (WS & SSE /events)")
        print("  - Stream D (Orchestrator): http://localhost:8080 (WS /ws/stream & SSE /events)")
        print("Press Ctrl+C to terminate both services.\n")

        while True:
            for p in procs:
                if p.poll() is not None:
                    print(f"Process {p.pid} exited with code {p.returncode}")
                    return
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down Role 1 services...")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except Exception:
                    p.kill()


if __name__ == "__main__":
    main()
