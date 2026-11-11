"""Настройки бота — читаются из файла .env рядом с bot.py.

Ничего править здесь не нужно. Все значения — в .env.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    """Простой разбор .env: KEY=VALUE, комментарии с #."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env(BASE_DIR / ".env")

# токен от @BotFather — обязателен
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# чей это бот: username владельца без @.
# Как только этот человек нажмёт /start, бот запомнит его и будет
# присылать туда все заявки.
#
# mikerover — значение по умолчанию, чтобы бот работал и без настроек.
# Если ник сменится — впиши новый здесь или задай ADMIN_USERNAME в .env.
ADMIN_USERNAME = (
    os.environ.get("ADMIN_USERNAME", "").strip().lstrip("@").lower() or "mikerover"
)

# база с заявками
# На обычном сервере — рядом с ботом.
# На хостинге BotHost — в /app/data (там данные переживают перезапуск).
def _pick_db_path() -> Path:
    import os as _os

    candidate = Path(_os.environ.get("DATA_DIR") or "/app/data")
    if candidate.is_dir():
        try:
            probe = candidate / ".write_test"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            return candidate / "bot.db"
        except OSError:
            pass
    return BASE_DIR / "bot.db"


DB_PATH = _pick_db_path()

# сколько секунд работать (0 = бесконечно).
# Нужно только для локального теста, на сервере всегда 0.
WORK_SECONDS = int(os.environ.get("WORK_SECONDS", "0") or "0")
