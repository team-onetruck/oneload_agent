"""`.env` 준비 · 읽기.

- 저장소 루트에 `.env` 가 없으면 `.env.example` 을 복사해 만든다 (키 칸은 비어 있음).
- `.env` 값을 환경 변수로 읽는다. 이미 터미널 · 서버에 설정된 값이 있으면 그 값을 우선한다 (override=False).
- `.env` 는 .gitignore 로 제외되어 git 에 올라가지 않는다.

직접 실행하면 `.env` 를 만들고 위치를 알려 준다:  python -m hangil_r2.config
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_NAME = "OPENAI_API_KEY"


def ensure_env(root: Path = ROOT) -> tuple[Path, bool]:
    """루트에 .env 가 없으면 .env.example 로 만든다. (경로, 새로 만들었는지) 를 돌려준다."""
    env, example = root / ".env", root / ".env.example"
    if env.exists() or not example.exists():  # 패키지로 설치된 경우(예제 파일 없음)는 아무것도 만들지 않음
        return env, False
    shutil.copyfile(example, env)
    return env, True


def load_env(root: Path = ROOT) -> Path:
    """.env 를 (없으면 만들고) 읽는다. python-dotenv 가 없으면 조용히 건너뛴다 (서버는 환경 변수로 받음)."""
    env, _ = ensure_env(root)
    if env.exists():
        try:
            from dotenv import load_dotenv
        except ImportError:
            return env
        load_dotenv(env, override=False)
    return env


def key_problem(root: Path = ROOT) -> str | None:
    """키가 비어 있으면 무엇을 고쳐야 하는지 한국어로 돌려준다. 문제 없으면 None. 키 값 자체는 절대 출력하지 않는다."""
    env = load_env(root)
    if os.getenv(KEY_NAME, "").strip():
        return None
    return f"{KEY_NAME} 가 비어 있어요. 이 파일을 열어 '{KEY_NAME}=' 뒤에 키를 붙여 넣고 저장하세요:\n  {env}"


if __name__ == "__main__":
    path, created = ensure_env()
    print(("새로 만들었어요: " if created else "이미 있어요: ") + str(path))
    problem = key_problem()
    print(problem or "키가 들어 있어요. 이제 python eval/run_eval.py agent --llm openai 로 실행하세요.")
