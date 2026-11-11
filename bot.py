"""Бот студии видеопоздравлений.

Принимает заявку за 6 шагов, присылает её владельцу в Telegram,
сохраняет в базу bot.db.

Только стандартная библиотека Python — ничего устанавливать не нужно.

Запуск на сервере:  systemctl start video-bot
Локально:           run_local.bat
"""

from __future__ import annotations

import html
import json
import sys
import time
import traceback
import urllib.error
import urllib.request

import config
import texts
from storage import Storage

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
MAX_PHOTOS = 10

db = Storage(config.DB_PATH)


# ------------------------------------------------------------- Telegram API ---
def call(method: str, payload: dict | None = None, timeout: int = 35) -> dict:
    """Один запрос к Bot API."""
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{API}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def esc(value: object) -> str:
    """Экранирование пользовательского текста для parse_mode=HTML."""
    return html.escape(str(value or ""), quote=False)


def send(chat_id: int, text: str, keyboard: dict | None = None) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        call("sendMessage", payload)
    except urllib.error.HTTPError as exc:
        print(f"[send] HTTP {exc.code}: {exc.read()[:300]!r}", flush=True)


def send_photos(chat_id: int, photos: list[str], caption: str | None = None) -> None:
    """Отправляет фото клиента владельцу. Используются file_id — файлы не качаем."""
    if not photos:
        return
    try:
        if len(photos) == 1:
            payload = {"chat_id": chat_id, "photo": photos[0]}
            if caption:
                payload["caption"] = caption[:1000]
            call("sendPhoto", payload)
            return
        media = [{"type": "photo", "media": fid} for fid in photos[:MAX_PHOTOS]]
        if caption:
            media[0]["caption"] = caption[:1000]
        call("sendMediaGroup", {"chat_id": chat_id, "media": media})
    except urllib.error.HTTPError as exc:
        print(f"[photos] HTTP {exc.code}: {exc.read()[:300]!r}", flush=True)


# ------------------------------------------------------------------ анкета ---
def start_order(chat_id: int) -> None:
    db.set_state(chat_id, "occasion", {}, [])
    send(chat_id, texts.STEP_OCCASION, texts.occasion_kb())


def restart_order(chat_id: int) -> None:
    send(chat_id, texts.RESTARTED)
    start_order(chat_id)


def cancel_order(chat_id: int) -> None:
    db.clear_state(chat_id)
    send(chat_id, texts.CANCELLED, texts.main_menu())


def handle_photo(chat_id: int, message: dict) -> None:
    state = db.get_state(chat_id)
    if not state or state["step"] != "photos":
        send(chat_id, texts.HINT, texts.keyboard())
        return

    photos = state["photos"]
    if len(photos) >= MAX_PHOTOS:
        send(chat_id, texts.PHOTO_FULL, texts.photos_kb())
        return

    # последний элемент массива — самый большой размер
    file_id = message["photo"][-1]["file_id"]
    photos.append(file_id)
    db.set_state(chat_id, "photos", state["data"], photos)

    # не отвечаем на каждое фото альбома — иначе 10 одинаковых сообщений
    count = len(photos)
    if count == 1 or count == MAX_PHOTOS or count % 3 == 0:
        send(chat_id, texts.PHOTO_ADDED.format(n=count), texts.photos_kb())


def finish_photos(chat_id: int, with_photos: bool) -> None:
    state = db.get_state(chat_id)
    if not state or state["step"] != "photos":
        send(chat_id, texts.NOT_NOW)
        return
    photos = state["photos"] if with_photos else []
    data = state["data"]
    db.set_state(chat_id, "contact", data, photos)
    send(chat_id, texts.STEP_CONTACT)


def finish_contact(chat_id: int, text: str) -> None:
    state = db.get_state(chat_id)
    if not state or state["step"] != "contact":
        send(chat_id, texts.NOT_NOW)
        return

    if len(text) < 2 or not any(ch.isdigit() or ch == "@" for ch in text):
        send(chat_id, texts.BAD_CONTACT)
        return

    data = state["data"]
    data["contact"] = text[:200]
    data["photos_count"] = len(state["photos"])
    db.set_state(chat_id, "confirm", data, state["photos"])
    send(chat_id, texts.summarize(data), texts.confirm_kb())


def submit_order(chat_id: int, username: str) -> None:
    state = db.get_state(chat_id)
    if not state or state["step"] != "confirm":
        send(chat_id, texts.NOT_NOW)
        return

    data = state["data"]
    photos = state["photos"]
    order_id = db.save_order(chat_id, username, data, photos)
    db.clear_state(chat_id)

    send(chat_id, texts.CLIENT_DONE.format(number=order_id), texts.main_menu())
    notify_admin(order_id, chat_id, username, data, photos)


def notify_admin(
    order_id: int,
    chat_id: int,
    username: str,
    data: dict,
    photos: list[str],
) -> None:
    admin_id = db.get_setting("admin_id")
    if not admin_id:
        print(texts.ADMIN_WARNING, flush=True)
        return

    lines = [
        texts.ADMIN_HEADER.format(number=order_id),
        "",
        f"🎉 <b>Повод:</b> {esc(data.get('occasion'))}",
        f"👤 <b>Кого:</b> {esc(data.get('whom'))}",
        f"📅 <b>Дата:</b> {esc(data.get('date'))}",
        f"🎬 <b>Формат:</b> {esc(data.get('format'))}",
        f"💰 <b>Цена:</b> {esc(data.get('price'))} ₽",
        f"✍️ <b>Пожелания:</b> {esc(data.get('wishes'))}",
        f"📞 <b>Контакт:</b> {esc(data.get('contact'))}",
        f"🖼 <b>Фото:</b> {len(photos)} шт.",
    ]
    if username:
        lines.append(f"🔗 <b>Профиль:</b> @{esc(username)}")
    lines.append("")
    lines.append("Ответьте клиенту прямо в этот чат или по кнопке ниже.")

    send(int(admin_id), "\n".join(lines), texts.client_kb(username, chat_id))
    send_photos(int(admin_id), photos, caption=f"Фото к заявке №{order_id}")


# ------------------------------------------------------------------ роутер ---
def is_admin(user: dict) -> bool:
    """Если это владелец из ADMIN_USERNAME — запоминаем, куда слать заявки."""
    username = (user.get("username") or "").lower()
    if config.ADMIN_USERNAME and username == config.ADMIN_USERNAME:
        db.set_setting("admin_id", str(user["id"]))
        return True
    return False


def handle_message(message: dict) -> None:
    chat_id = message["chat"]["id"]
    user = message.get("from") or {}
    text = (message.get("text") or "").strip()
    lower = text.lower()

    is_admin(user)

    # --- фото ---
    if message.get("photo"):
        handle_photo(chat_id, message)
        return
    if message.get("document"):
        state_now = db.get_state(chat_id)
        if state_now and state_now["step"] == "photos":
            send(chat_id, texts.PHOTO_AS_FILE)
        else:
            send(chat_id, texts.HINT, texts.keyboard())
        return

    # --- команды ---
    if lower.startswith("/start"):
        send(chat_id, texts.WELCOME, texts.keyboard())
        send(chat_id, texts.MENU_HINT, texts.main_menu())
        return

    if lower.startswith("/cancel") or lower in {"отмена", "стоп"}:
        cancel_order(chat_id)
        return

    if lower.startswith("/order") or lower.startswith("/zayavka"):
        restart_order(chat_id)
        return

    if lower.startswith("/myid"):
        send(chat_id, f"Ваш chat_id: <code>{chat_id}</code>")
        return

    if lower.startswith("/help"):
        send(chat_id, texts.DETAILS, texts.keyboard())
        return

    # --- кнопки нижнего меню ---
    if text == texts.BTN_EXAMPLES:
        send(chat_id, texts.EXAMPLES, texts.keyboard())
        return
    if text == texts.BTN_PRICES:
        send(chat_id, texts.PRICES, texts.order_keyboard())
        return
    if text == texts.BTN_ORDER:
        start_order(chat_id)
        return
    if text == texts.BTN_DETAILS:
        send(chat_id, texts.DETAILS, texts.keyboard())
        return
    if text == texts.BTN_QUESTION:
        send(chat_id, texts.QUESTION, texts.main_menu())
        return

    # --- шаги анкеты ---
    state = db.get_state(chat_id)
    if state and state["step"]:
        handle_step(chat_id, state, text)
        return

    send(chat_id, texts.HINT, texts.keyboard())


def handle_step(chat_id: int, state: dict, text: str) -> None:
    step = state["step"]
    data = state["data"]
    photos = state["photos"]

    if step == "occasion":
        send(chat_id, "Выберите повод кнопкой ниже 👇", texts.occasion_kb())
        return

    if step == "whom":
        if len(text) < 2:
            send(chat_id, texts.BAD_TEXT)
            return
        data["whom"] = text[:200]
        db.set_state(chat_id, "date", data, photos)
        send(chat_id, texts.STEP_DATE)
        return

    if step == "date":
        if len(text) < 2:
            send(chat_id, texts.BAD_TEXT)
            return
        data["date"] = text[:100]
        db.set_state(chat_id, "format", data, photos)
        send(chat_id, texts.STEP_FORMAT, texts.format_kb())
        return

    if step == "format":
        send(chat_id, "Выберите формат кнопкой 👇", texts.format_kb())
        return

    if step == "wishes":
        if len(text) < 2:
            send(chat_id, texts.BAD_TEXT, texts.wishes_kb())
            return
        data["wishes"] = text[:1000]
        db.set_state(chat_id, "photos", data, photos)
        send(chat_id, texts.STEP_PHOTOS, texts.photos_kb())
        return

    if step == "photos":
        send(chat_id, "Пришлите фото или нажмите «Готово» 👇", texts.photos_kb())
        return

    if step == "contact":
        finish_contact(chat_id, text)
        return

    if step == "confirm":
        send(chat_id, "Проверьте заявку и нажмите «Отправить» 👇", texts.confirm_kb())
        return

    db.clear_state(chat_id)
    send(chat_id, texts.HINT, texts.keyboard())


def handle_callback(query: dict) -> None:
    chat_id = query["message"]["chat"]["id"]
    user = query.get("from") or {}
    payload = query.get("data") or ""

    try:
        call("answerCallbackQuery", {"callback_query_id": query["id"]})
    except Exception:
        pass

    is_admin(user)
    state = db.get_state(chat_id)

    # --- общие кнопки ---
    if payload == "order":
        start_order(chat_id)
        return

    if payload == "details":
        send(chat_id, texts.DETAILS, texts.keyboard())
        return

    # --- шаг 1: повод ---
    if payload.startswith("occ:"):
        if not state or state["step"] != "occasion":
            send(chat_id, texts.NOT_NOW)
            return
        key = payload.split(":", 1)[1]
        state["data"]["occasion"] = texts.OCCASION_TITLES.get(key, key)
        db.set_state(chat_id, "whom", state["data"], state["photos"])
        send(chat_id, texts.STEP_WHOM)
        return

    # --- шаг 4: формат ---
    if payload.startswith("fmt:"):
        if not state or state["step"] != "format":
            send(chat_id, texts.NOT_NOW)
            return
        key = payload.split(":", 1)[1]
        chosen = next((f for f in texts.FORMATS if f["key"] == key), None)
        if not chosen:
            send(chat_id, texts.NOT_NOW)
            return
        state["data"]["format"] = chosen["label"]
        state["data"]["price"] = chosen["price"]
        db.set_state(chat_id, "wishes", state["data"], state["photos"])
        send(chat_id, texts.STEP_WISHES, texts.wishes_kb())
        return

    # --- шаг 5: «придумайте сами» ---
    if payload == "wish:skip":
        if not state or state["step"] != "wishes":
            send(chat_id, texts.NOT_NOW)
            return
        state["data"]["wishes"] = "Придумайте сценарий сами"
        db.set_state(chat_id, "photos", state["data"], state["photos"])
        send(chat_id, texts.STEP_PHOTOS, texts.photos_kb())
        return

    # --- шаг 6: фото ---
    if payload == "ph:done":
        finish_photos(chat_id, True)
        return

    if payload == "ph:skip":
        finish_photos(chat_id, False)
        return

    # --- подтверждение ---
    if payload == "cf:send":
        submit_order(chat_id, user.get("username") or "")
        return

    if payload == "cf:restart":
        restart_order(chat_id)
        return

    if payload == "cf:cancel":
        cancel_order(chat_id)
        return

    send(chat_id, texts.NOT_NOW)


def handle_update(update: dict) -> None:
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])


# -------------------------------------------------------------------- цикл ---
def run() -> int:
    if not config.BOT_TOKEN:
        print("ОШИБКА: не задан BOT_TOKEN.")
        print("Локально: создайте файл .env со строкой BOT_TOKEN=ваш_токен")
        print("На сервере: nano ~/VideoBot/.env")
        return 1

    print("Бот запущен.", flush=True)
    print(f"Админ по username: @{config.ADMIN_USERNAME or 'не задан'}", flush=True)
    print(f"Заявок в базе: {db.count_orders()}", flush=True)

    deadline = time.monotonic() + config.WORK_SECONDS if config.WORK_SECONDS > 0 else None
    offset: int | None = None
    handled = 0

    while True:
        if deadline is not None and time.monotonic() >= deadline:
            break

        params: dict = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset

        try:
            response = call("getUpdates", params, timeout=40)
        except urllib.error.HTTPError as exc:
            body = exc.read()[:300]
            print(f"[poll] HTTP {exc.code}: {body!r}", flush=True)
            if exc.code in (401, 404):
                print("Токен неверный. Возьмите новый у @BotFather (/token).", flush=True)
                return 1
            time.sleep(5)
            continue
        except Exception as exc:
            print(f"[poll] сеть: {exc}", flush=True)
            time.sleep(5)
            continue

        for update in response.get("result", []):
            offset = update["update_id"] + 1
            try:
                handle_update(update)
                handled += 1
            except Exception as exc:
                print(f"[handle] ошибка: {exc!r}", flush=True)
                traceback.print_exc()

    print(f"Готово. Обработано обновлений: {handled}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    finally:
        db.close()
