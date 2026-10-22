""".env 준비 · 읽기 — 임시 폴더에서 시험 (실제 저장소의 .env 는 건드리지 않음)."""

from hangil_r2.config import KEY_NAME, ensure_env, key_problem


def make_root(tmp_path, example="OPENAI_API_KEY=\nOPENAI_MODEL=gpt-4o-mini\n"):
    (tmp_path / ".env.example").write_text(example, encoding="utf-8")
    return tmp_path


def test_creates_env_from_example_once(tmp_path):  # C-01
    root = make_root(tmp_path)
    env, created = ensure_env(root)
    assert created and env.read_text(encoding="utf-8").startswith("OPENAI_API_KEY=")
    env.write_text("OPENAI_API_KEY=filled\n", encoding="utf-8")
    _, created_again = ensure_env(root)
    assert not created_again and env.read_text(encoding="utf-8") == "OPENAI_API_KEY=filled\n"  # 덮어쓰지 않음


def test_empty_key_points_to_the_file(tmp_path, monkeypatch):  # C-02
    monkeypatch.delenv(KEY_NAME, raising=False)
    root = make_root(tmp_path)
    problem = key_problem(root)
    assert problem and str(root / ".env") in problem


def test_filled_key_is_read_but_never_printed(tmp_path, monkeypatch, capsys):  # C-03
    monkeypatch.delenv(KEY_NAME, raising=False)
    root = make_root(tmp_path)
    ensure_env(root)
    (root / ".env").write_text("OPENAI_API_KEY=sk-test-not-a-real-key\n", encoding="utf-8")
    assert key_problem(root) is None
    assert "sk-test" not in capsys.readouterr().out


def test_existing_env_var_wins(tmp_path, monkeypatch):  # C-04 서버 · 터미널에 이미 설정된 값을 덮지 않음
    monkeypatch.setenv(KEY_NAME, "from-server")
    root = make_root(tmp_path)
    assert key_problem(root) is None
    import os

    assert os.environ[KEY_NAME] == "from-server"
