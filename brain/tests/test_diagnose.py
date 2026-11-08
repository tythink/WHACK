"""Unit tests for Brain diagnosis engine, multi-turn tool loop, chat, wiki update, and FastAPI routes."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from brain.server import app

client = TestClient(app)


def _clear_all_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _init_temp_git_workspace(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "utils.py").write_text(
        "def calculate_window_offsets(total_items: int, window_size: int):\n"
        "    offsets = []\n"
        "    if total_items <= 0 or window_size <= 0:\n"
        "        return offsets\n"
        "    for i in range(0, total_items - window_size + 2):\n"
        "        offsets.append((i, i + window_size))\n"
        "    return offsets\n\n"
        "def batch_slice(data, offsets):\n"
        "    out = []\n"
        "    for start, end in offsets:\n"
        "        if end > len(data):\n"
        "            raise IndexError(f'Offset end {end} exceeds data length {len(data)}')\n"
        "        out.append(data[start:end])\n"
        "    return out\n",
        encoding="utf-8",
    )
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_utils.py").write_text(
        "from utils import calculate_window_offsets, batch_slice\n\n"
        "def test_sliding_window():\n"
        "    data = [10, 20, 30, 40, 50]\n"
        "    offsets = calculate_window_offsets(len(data), 3)\n"
        "    assert batch_slice(data, offsets) == [[10, 20, 30], [20, 30, 40], [30, 40, 50]]\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "chef@whack.local"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Chef Ramsay"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit with bug"], cwd=str(repo_dir), check=True, capture_output=True)
    return repo_dir


def test_health_endpoint(monkeypatch: pytest.MonkeyPatch):
    _clear_all_api_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("LLM_MODEL", raising=False)

    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert data["service"] == "brain"
    assert data["provider"] == "gemini"
    assert data["model"] == "gemini-3.1-flash-lite-preview"
    assert data["apiKeyConfigured"] is False
    assert "defaultRepo" in data
    assert "available_tools" in data


def test_hot_reload_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify creating/updating `.env` while Brain server is running hot-reloads API key without restart."""
    import brain.config as cfg

    _clear_all_api_keys(monkeypatch)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    fake_env = tmp_path / ".env"
    monkeypatch.setattr(cfg, "ENV_CANDIDATES", (fake_env,))
    monkeypatch.setattr(cfg, "_ENV_FILE_SIGNATURES", {fake_env: None})

    assert client.get("/health").json()["apiKeyConfigured"] is False

    fake_env.write_text("GEMINI_API_KEY=test-live-hot-reload-key\n", encoding="utf-8")
    health_after = client.get("/health").json()
    assert health_after["apiKeyConfigured"] is True
    assert health_after["provider"] == "gemini"


def test_get_diagnose_stub_removed():
    """Verify dummy GET /diagnose stub route has been removed."""
    res = client.get("/diagnose")
    assert res.status_code == 405


def test_offline_diagnosis_when_missing_api_key(monkeypatch: pytest.MonkeyPatch):
    """When no API key is configured, POST /diagnose returns honest source='offline' (never 'heuristic')."""
    _clear_all_api_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    payload = {
        "hintLevel": "hint",
        "captureScreenshot": False,
        "editorState": {"activeFile": "utils.py", "cursorLine": 11},
        "terminalState": {
            "command": "pytest tests/",
            "exitCode": 1,
            "tailOutput": "IndexError: Offset end 6 exceeds data length 5",
        },
    }
    res = client.post("/diagnose", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "offline"
    assert data["uncertain"] is True
    assert data["confidence"] == 0.0
    assert data["error"] and "GEMINI_API_KEY" in data["error"]
    assert data["file"] is None
    assert data["lines"] is None
    assert data["suggestedFix"] is None
    assert "stove" in data["quip"].lower()
    assert data["diagnosisId"]


def test_gemini_multi_turn_tool_loop_and_alternate_request_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo_dir = _init_temp_git_workspace(tmp_path / "ws")
    _clear_all_api_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-fake-gemini-key")

    calls_seen = []

    class FakeModels:
        def generate_content(self, model, contents, config=None):
            calls_seen.append((model, contents, config))
            # Ensure tools and response_mime_type="application/json" are NEVER combined
            if config is not None and getattr(config, "tools", None):
                assert getattr(config, "response_mime_type", None) != "application/json"

            if len(calls_seen) == 1:
                return SimpleNamespace(
                    function_calls=[
                        SimpleNamespace(
                            name="read_file_range",
                            args={"rel_path": "utils.py", "start_line": 1, "end_line": 10},
                        )
                    ],
                    candidates=[],
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=100, candidates_token_count=20, total_token_count=120
                    ),
                )

            fenced = (
                "Look at that loop bound — it is RAW!\n"
                "```json\n"
                "{\n"
                '  "summary": "Off-by-one stop condition in calculate_window_offsets",\n'
                '  "file": "utils.py",\n'
                '  "lines": "5-5",\n'
                '  "quip": "This loop took an extra lap past the kitchen pass!",\n'
                '  "uncertain": false,\n'
                '  "confidence": 94,\n'
                '  "hintLevel": "full_explanation",\n'
                '  "explanation": "Change `+ 2` to `+ 1` on line 5.",\n'
                '  "citations": [{"file": "utils.py", "line": 5, "snippet": "for i in range(...)"}],\n'
                '  "suggestedFix": {\n'
                '    "file": "utils.py",\n'
                '    "line": 5,\n'
                '    "oldText": "for i in range(0, total_items - window_size + 2):",\n'
                '    "newText": "for i in range(0, total_items - window_size + 1):"\n'
                "  }\n"
                "}\n"
                "```"
            )
            return SimpleNamespace(
                function_calls=[],
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(thought=True, text="Checking exact indentation..."),
                                SimpleNamespace(thought=False, text=fenced),
                            ]
                        )
                    )
                ],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=150, candidates_token_count=80, total_token_count=230
                ),
            )

    class FakeGeminiClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.models = FakeModels()

    monkeypatch.setattr("google.genai.Client", FakeGeminiClient)

    payload = {
        "hintLevel": "full",
        "workspaceRoot": str(repo_dir),
        "captureScreenshot": False,
        "user_status": "stuck_on_test",
        "fusionReasons": ["repeated_exit_code_1", "frustration_spike"],
        "emotions": {"frustration": 0.84, "confusion": 0.71, "surprise": 0.12, "neutral": 0.08},
        "editorState": {
            "activeFile": "utils.py",
            "cursorLine": 5,
            "diagnosticsSummary": "IndexError in sliding window batch_slice",
        },
        "terminalState": {
            "command": "pytest tests/",
            "exitCode": 1,
            "tailOutput": "IndexError: Offset end 6 exceeds data length 5",
        },
    }

    res = client.post("/diagnose", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["source"] == "model"
    assert data["error"] is None
    assert data["file"] == "utils.py"
    assert data["lines"] == [5, 5]
    assert data["confidence"] == 0.94
    assert data["hintLevel"] == "full_explanation"
    assert data["repoPath"] == str(repo_dir.resolve())
    assert data["reasons"] == ["repeated_exit_code_1", "frustration_spike"]
    assert data["trace"]["steps_used"] == 2
    assert data["trace"]["tools_called"][0]["tool"] == "read_file_range"
    # Verify indentation-aligned StructuredEdit preserved exact 4-space indent from utils.py
    assert data["suggestedFix"] is not None
    assert data["suggestedFix"]["oldText"] == "    for i in range(0, total_items - window_size + 2):"
    assert data["suggestedFix"]["newText"] == "    for i in range(0, total_items - window_size + 1):"


def test_openai_and_anthropic_multi_turn_tool_loops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo_dir = _init_temp_git_workspace(tmp_path / "ws_multi")

    # 1. OpenAI tool loop
    _clear_all_api_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-openai")

    oai_turns = []

    class FakeCompletions:
        def create(self, model, messages, tools=None):
            oai_turns.append((model, list(messages), tools))
            assert model == "gpt-4o-mini"
            if len(oai_turns) == 1:
                tc = SimpleNamespace(
                    id="call_abc",
                    function=SimpleNamespace(
                        name="search_text",
                        arguments=json.dumps({"query": "window_size + 2"}),
                    ),
                )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))],
                    usage=SimpleNamespace(prompt_tokens=50, completion_tokens=15, total_tokens=65),
                )
            assert any(m.get("role") == "tool" for m in messages if isinstance(m, dict))
            ans = json.dumps({
                "summary": "Off-by-one loop stop in utils.py",
                "file": "utils.py",
                "lines": [5, 5],
                "quip": "One extra slice off the counter!",
                "uncertain": False,
                "confidence": 0.91,
                "hintLevel": "hint",
            })
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=ans, tool_calls=None))],
                usage=SimpleNamespace(prompt_tokens=80, completion_tokens=40, total_tokens=120),
            )

    class FakeOpenAI:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    res_oai = client.post(
        "/diagnose",
        json={"hintLevel": "hint", "repoPath": str(repo_dir), "captureScreenshot": False},
    )
    assert res_oai.status_code == 200
    d_oai = res_oai.json()
    assert d_oai["source"] == "model"
    assert d_oai["trace"]["tools_called"][0]["tool"] == "search_text"

    # 2. Anthropic tool loop
    _clear_all_api_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    anth_turns = []

    class FakeAnthropicMessages:
        def create(self, model, max_tokens, system, messages, tools=None):
            anth_turns.append((model, list(messages), tools))
            assert model == "claude-3-5-haiku-latest"
            if len(anth_turns) == 1:
                return SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            id="toolu_01",
                            name="read_file_range",
                            input={"rel_path": "utils.py", "start_line": 1, "end_line": 8},
                        )
                    ],
                    usage=SimpleNamespace(input_tokens=60, output_tokens=20),
                )
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="text",
                        text=json.dumps({
                            "summary": "Off-by-one in calculate_window_offsets",
                            "file": "utils.py",
                            "lines": [5, 5],
                            "quip": "Pull that range back by 1!",
                            "uncertain": False,
                            "confidence": 0.93,
                        }),
                    )
                ],
                usage=SimpleNamespace(input_tokens=90, output_tokens=35),
            )

    class FakeAnthropicClient:
        def __init__(self, api_key):
            self.messages = FakeAnthropicMessages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropicClient))

    res_anth = client.post(
        "/diagnose",
        json={"hintLevel": "hint", "repoPath": str(repo_dir), "captureScreenshot": False},
    )
    assert res_anth.status_code == 200
    d_anth = res_anth.json()
    assert d_anth["source"] == "model"
    assert d_anth["trace"]["tools_called"][0]["tool"] == "read_file_range"


def test_post_diagnose_streaming_sse(monkeypatch: pytest.MonkeyPatch):
    _clear_all_api_keys(monkeypatch)
    payload = {
        "hintLevel": "nudge",
        "stream": True,
        "captureScreenshot": False,
        "terminalState": {"tailOutput": "IndexError: Offset end 6 exceeds data length 5"},
    }
    res = client.post("/diagnose", json=payload)
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    text = res.text
    assert "event: start" in text
    assert "event: diagnosis" in text
    assert "event: done" in text


def test_post_chat_offline_and_live_never_broadcasts_to_core(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo_dir = _init_temp_git_workspace(tmp_path / "chat_ws")
    broadcast_calls = []

    async def fake_broadcast(payload):
        broadcast_calls.append(payload)

    monkeypatch.setattr("brain.server.broadcast_to_core", fake_broadcast)
    monkeypatch.setattr("brain.agent.broadcast_to_core", fake_broadcast)

    # 1. Offline chat when no API key is configured
    _clear_all_api_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    res_off = client.post(
        "/chat",
        json={"message": "Why did batch_slice raise IndexError?", "repoPath": str(repo_dir)},
    )
    assert res_off.status_code == 200
    off_data = res_off.json()
    assert off_data["mode"] == "offline"
    assert off_data["error"]
    assert len(broadcast_calls) == 0

    # 2. Live chat with mocked Gemini
    monkeypatch.setenv("GEMINI_API_KEY", "fake-chat-key")

    class FakeChatModels:
        def generate_content(self, model, contents, config=None):
            reply = (
                "Because `utils.py:5` uses `total_items - window_size + 2`, producing an extra window `(3, 6)`!\n\n"
                '<whack_fix>{"file": "utils.py", "line": 5, '
                '"oldText": "for i in range(0, total_items - window_size + 2):", '
                '"newText": "for i in range(0, total_items - window_size + 1):"}</whack_fix>'
            )
            return SimpleNamespace(
                function_calls=[],
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(parts=[SimpleNamespace(thought=False, text=reply)])
                    )
                ],
                usage_metadata=SimpleNamespace(prompt_token_count=50, candidates_token_count=30, total_token_count=80),
            )

    monkeypatch.setattr(
        "google.genai.Client",
        lambda api_key: SimpleNamespace(models=FakeChatModels()),
    )

    res_live = client.post(
        "/chat",
        json={
            "message": "Can you show me the exact fix for utils.py?",
            "activeFile": "utils.py",
            "highlightedLines": [5, 6],
            "repoPath": str(repo_dir),
            "history": [{"role": "user", "content": "Hi chef"}],
        },
    )
    assert res_live.status_code == 200
    live_data = res_live.json()
    assert live_data["mode"] == "brain_live"
    assert live_data["error"] is None
    assert "<whack_fix>" not in live_data["reply"]
    assert live_data["suggestedFix"]["oldText"] == "    for i in range(0, total_items - window_size + 2):"
    # POST /chat MUST NEVER call broadcast_to_core!
    assert len(broadcast_calls) == 0


def test_post_wiki_update_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    recorded = {}

    def fake_update_repo_wiki(repo_dir, allow_model=True, force=False):
        recorded["repo_dir"] = repo_dir
        recorded["allow_model"] = allow_model
        recorded["force"] = force
        return {"updated": True, "repo": str(repo_dir), "modules": ["utils"]}

    monkeypatch.setattr("brain.server.update_repo_wiki", fake_update_repo_wiki)

    res = client.post(
        "/wiki/update",
        json={"repoPath": str(tmp_path), "allowModel": False, "force": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["updated"] is True
    assert recorded["repo_dir"] == tmp_path.resolve()
    assert recorded["allow_model"] is False
    assert recorded["force"] is True


def test_apply_fix_and_rollback(tmp_path: Path):
    """Test full cycle: /apply-fix applies edit in target workspace and /rollback reverts it."""
    repo_dir = _init_temp_git_workspace(tmp_path / "fix_ws")

    payload = {
        "repoPath": str(repo_dir),
        "fix": {
            "file": "utils.py",
            "startLine": 5,
            "endLine": 5,
            "oldText": "    for i in range(0, total_items - window_size + 2):",
            "newText": "    for i in range(0, total_items - window_size + 1):",
        },
    }
    res = client.post("/apply-fix", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["before_passed"] is False
    assert data["after_passed"] is True
    checkpoint_hash = data["checkpoint_hash"]

    rb_res = client.post(
        "/rollback",
        json={"checkpointHash": checkpoint_hash, "repoPath": str(repo_dir)},
    )
    assert rb_res.status_code == 200
    rb_data = rb_res.json()
    assert rb_data["success"] is True
    assert rb_data["rolled_back_to"] == checkpoint_hash
    assert rb_data["tests_passed"] is False


def test_apply_fix_mismatch_refused(tmp_path: Path):
    repo_dir = _init_temp_git_workspace(tmp_path / "mismatch_ws")
    payload = {
        "repoPath": str(repo_dir),
        "fix": {
            "file": "utils.py",
            "startLine": 5,
            "endLine": 5,
            "oldText": "    for i in range(0, completely_wrong_text):",
            "newText": "    for i in range(0, total_items - window_size + 1):",
        },
    }
    res = client.post("/apply-fix", json=payload)
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert "Refused" in data["error"] or "did not match" in data["error"]


def test_apply_fix_sandbox_violation(tmp_path: Path):
    repo_dir = _init_temp_git_workspace(tmp_path / "sandbox_ws")
    payload = {
        "repoPath": str(repo_dir),
        "fix": {
            "file": "../outside.py",
            "startLine": 1,
            "endLine": 1,
            "oldText": "something",
            "newText": "hacked",
        },
    }
    res = client.post("/apply-fix", json=payload)
    assert res.status_code in (400, 403)
