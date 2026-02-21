import os
import json
from datetime import datetime, timezone, date

from flask import Flask, request, send_from_directory, jsonify
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========= ENV =========
BOT_TOKEN = os.environ["BOT_TOKEN"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")  # https://xxxx.onrender.com

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
app = Flask(__name__)

# ========= Utils =========
def iso_now():
    return datetime.now(timezone.utc).isoformat()

def today_str():
    return date.today().iso(toggle:=False) if False else date.today().isoformat()  # безопасно, без локали

def tg_send(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)

def tg_send_photo(chat_id, photo_url, caption=""):
    payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption}
    requests.post(f"{TELEGRAM_API}/sendPhoto", json=payload, timeout=20)

def tg_answer_cb(cb_id):
    requests.post(
        f"{TELEGRAM_API}/answerCallbackQuery",
        json={"callback_query_id": cb_id},
        timeout=10
    )

def tg_get_file_url(file_id):
    r = requests.get(
        f"{TELEGRAM_API}/getFile",
        params={"file_id": file_id},
        timeout=20
    ).json()
    file_path = r["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

def open_app_kb():
    webapp_url = f"{PUBLIC_BASE_URL}/web/index.html"
    return {
        "inline_keyboard": [
            [{"text": "🔥 Открыть мини-приложение", "web_app": {"url": webapp_url}}],
            [{"text": "📸 Добавить еду (фото/текст)", "callback_data": "meal_prompt"}],
        ]
    }

def cancel_kb():
    return {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel"}]]}

# ========= Google Sheets =========
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

# ========= Sheet helpers =========
def find_row_by_user(ws, user_id):
    col = ws.col_values(1)
    try:
        return col.index(user_id) + 1
    except ValueError:
        return None

def upsert_user(ws_users, user_id, first_name, data):
    # users headers:
    # user_id, first_name, timezone, created_at,
    # height_cm, age, start_weight_kg, goal_weight_kg,
    # goal_deadline, activity_level, kcal_target, checkin_time, checkout_time
    row = [
        user_id,
        first_name,
        data.get("timezone", "Europe/Moscow"),
        data.get("created_at", iso_now()),
        str(data.get("height_cm", "")),
        str(data.get("age", "")),
        str(data.get("start_weight_kg", "")),
        str(data.get("goal_weight_kg", "")),
        str(data.get("goal_deadline", "")),
        str(data.get("activity_level", "medium")),
        str(data.get("kcal_target", "")),
        str(data.get("checkin_time", "08:05")),
        str(data.get("checkout_time", "22:30")),
    ]
    r = find_row_by_user(ws_users, user_id)
    if r:
        ws_users.update(f"A{r}:M{r}", [row])
    else:
        ws_users.append_row(row)

def state_set(ws_state, user_id, pending_action, last_prompt=""):
    # state: user_id, pending_action, pending_since, last_prompt
    r = find_row_by_user(ws_state, user_id)
    now = iso_now()
    row = [user_id, pending_action, now, last_prompt]
    if r:
        ws_state.update(f"A{r}:D{r}", [row])
    else:
        ws_state.append_row(row)

def state_get(ws_state, user_id):
    r = find_row_by_user(ws_state, user_id)
    if not r:
        return ""
    vals = ws_state.row_values(r)
    return vals[1] if len(vals) > 1 else ""

def state_clear(ws_state, user_id):
    r = find_row_by_user(ws_state, user_id)
    if not r:
        return
    ws_state.update(f"B{r}:D{r}", [["", "", ""]])

def daily_find_or_create(ws_daily, user_id, day):
    rows = ws_daily.get_all_values()
    for i in range(1, len(rows)):
        if len(rows[i]) >= 2 and rows[i][0] == day and rows[i][1] == user_id:
            return i + 1
    # date,user_id,weight_kg,steps,workout,water_ml,sleep_h,comment,kcal_eaten,kcal_left,updated_at...
    ws_daily.append_row([day, user_id, "", "", "", "", "", "", "", "", "", "", "", iso_now()])
    return len(rows) + 1

def daily_set(ws_daily, row, col, value):
    ws_daily.update_cell(row, col, value)
    ws_daily.update_cell(row, 14, iso_now())  # updated_at

# ========= Math =========
def calc_kcal_target(weight_kg, height_cm, age, activity, goal_weeks):
    # Mifflin-St Jeor (муж)
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    mult = {"low": 1.2, "medium": 1.375, "high": 1.55}.get(activity, 1.375)
    tdee = bmr * mult

    deficit = tdee * 0.20
    kcal_target = max(1500, int(tdee - deficit))
    steps_target = 9000 if activity != "high" else 11000

    if goal_weeks is not None and goal_weeks <= 10:
        kcal_target = max(1500, int(tdee - tdee * 0.25))

    return int(tdee), int(kcal_target), int(steps_target)

# ========= Meals estimation (MVP) =========
def estimate_text_kcal(text):
    t = (text or "").lower()
    kcal = 0
    if "яйц" in t:
        kcal += 160
    if "хлеб" in t:
        kcal += 120
    if "печен" in t and "треск" in t:
        kcal += 480
    if "сахар" in t:
        kcal += 30
    return kcal if kcal > 0 else 500

def estimate_photo_kcal(_photo_url):
    return 600

# ========= Totals =========
def sum_today_kcal(ws_meals, user_id, day):
    rows = ws_meals.get_all_values()
    total = 0
    for i in range(1, len(rows)):
        r = rows[i]
        if len(r) < 8:
            continue
        ts = r[0]
        uid = r[1] if len(r) > 1 else ""
        if uid != user_id:
            continue
        if ts[:10] != day:
            continue
        try:
            total += int(float(r[7] or "0"))
        except Exception:
            pass
    return total

def get_user_targets(ws_users, user_id):
    r = find_row_by_user(ws_users, user_id)
    if not r:
        return None
    vals = ws_users.row_values(r)
    kcal_target = 2100
    if len(vals) > 10 and vals[10]:
        try:
            kcal_target = int(float(vals[10]))
        except Exception:
            kcal_target = 2100
    return {"kcal_target": kcal_target}

# ========= Web routes =========
@app.route("/", methods=["GET"])
def health():
    return "OK", 200

@app.route("/web/<path:filename>", methods=["GET"])
def web_files(filename):
    return send_from_directory("web", filename)

@app.route("/api/today", methods=["GET"])
def api_today():
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"ok": False, "error": "user_id required"}), 400

    sh = get_sheet()
    ws_users = sh.worksheet("users")
    ws_meals = sh.worksheet("meals")
    ws_daily = sh.worksheet("daily_log")

    targets = get_user_targets(ws_users, user_id) or {"kcal_target": 2100}
    day = today_str()
    eaten = sum_today_kcal(ws_meals, user_id, day)
    left = max(0, targets["kcal_target"] - eaten)

    row = daily_find_or_create(ws_daily, user_id, day)
    vals = ws_daily.row_values(row)
    steps = 0
    if len(vals) > 4 and vals[4]:
        try:
            steps = int(vals[4])
        except Exception:
            steps = 0

    return jsonify({
        "ok": True,
        "date": day,
        "kcal_target": targets["kcal_target"],
        "kcal_eaten": eaten,
        "kcal_left": left,
        "steps": steps
    })

# ========= Telegram webhook =========
@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.args.get("secret", "") != WEBHOOK_SECRET:
        return "Forbidden", 403

    update = request.get_json(force=True)

    sh = get_sheet()
    ws_users = sh.worksheet("users")
    ws_meals = sh.worksheet("meals")
    ws_daily = sh.worksheet("daily_log")
    ws_state = sh.worksheet("state")

    # callbacks
    if "callback_query" in update:
        q = update["callback_query"]
        tg_answer_cb(q["id"])
        chat_id = q["message"]["chat"]["id"]
        user_id = str(q.get("from", {}).get("id", ""))
        data = q.get("data", "")

        if data == "meal_prompt":
            state_set(ws_state, user_id, "meal", "Ждём фото или текст еды")
            tg_send(chat_id, "Кидай фото еды 📸\nЕсли фото не получается — напиши текстом, что съел.", reply_markup=cancel_kb())
            return "OK", 200

        if data == "cancel":
            state_clear(ws_state, user_id)
            tg_send(chat_id, "Ок.", reply_markup=open_app_kb())
            return "OK", 200

        return "OK", 200

    msg = update.get("message")
    if not msg:
        return "OK", 200

    chat_id = msg["chat"]["id"]
    from_user = msg.get("from", {})
    user_id = str(from_user.get("id", ""))
    first_name = from_user.get("first_name", "")

    text = msg.get("text", "")

    # /start
    if text == "/start":
        photo_url = f"{PUBLIC_BASE_URL}/web/gipsy.jpg"
        caption = "🕯️ Старик коснулся плеча…\n— Худей."
        tg_send_photo(chat_id, photo_url, caption)
        tg_send(chat_id, "Открывай мини-приложение: там контракт, цифры и контроль.", reply_markup=open_app_kb())
        return "OK", 200

    # WebApp data
    if "web_app_data" in msg:
        try:
            payload = json.loads(msg["web_app_data"]["data"])
        except Exception:
            tg_send(chat_id, "Не понял данные из приложения.", reply_markup=open_app_kb())
            return "OK", 200

        action = payload.get("action", "")
        if action == "profile_save":
            try:
                w = float(payload.get("start_weight_kg"))
                h = float(payload.get("height_cm"))
                a = int(payload.get("age"))
                activity = payload.get("activity_level", "medium")
                goal_weeks = payload.get("goal_weeks")
                goal_weeks = float(goal_weeks) if goal_weeks not in (None, "", "null") else None
                _tdee, kcal_target, steps_target = calc_kcal_target(w, h, a, activity, goal_weeks)
            except Exception:
                kcal_target, steps_target = 2100, 9000

            payload["kcal_target"] = kcal_target
            payload["created_at"] = iso_now()

            upsert_user(ws_users, user_id, first_name, payload)

            tg_send(
                chat_id,
                f"Контракт принят ✅\nЛимит на день: ~{kcal_target} ккал.\nШаги: цель ~{steps_target}.\n\nТеперь добавляй еду — фото или текст.",
                reply_markup=open_app_kb()
            )
            return "OK", 200

        if action == "meal_request":
            state_set(ws_state, user_id, "meal", "Ждём фото или текст еды")
            tg_send(chat_id, "Кидай фото еды 📸\nЕсли фото не получается — напиши текстом, что съел.", reply_markup=cancel_kb())
            return "OK", 200

        if action == "weight_morning":
            w = str(payload.get("weight_morning_kg", "")).strip()
            day = today_str()
            row = daily_find_or_create(ws_daily, user_id, day)
            daily_set(ws_daily, row, 3, w)
            tg_send(chat_id, f"Вес записал ✅ {w} кг", reply_markup=open_app_kb())
            return "OK", 200

        if action == "steps":
            s = str(payload.get("steps", "")).strip()
            day = today_str()
            row = daily_find_or_create(ws_daily, user_id, day)
            daily_set(ws_daily, row, 5, s)
            tg_send(chat_id, f"Шаги записал ✅ {s}", reply_markup=open_app_kb())
            return "OK", 200

        tg_send(chat_id, "Ок.", reply_markup=open_app_kb())
        return "OK", 200

    pending = state_get(ws_state, user_id)

    # meal photo
    if "photo" in msg and pending == "meal":
        best = msg["photo"][-1]
        file_id = best["file_id"]
        photo_url = tg_get_file_url(file_id)
        kcal = estimate_photo_kcal(photo_url)

        ws_meals.append_row([iso_now(), user_id, "photo", "", "", file_id, photo_url, str(kcal), "0.35", "", "", "MVP"])
        state_clear(ws_state, user_id)

        day = today_str()
        targets = get_user_targets(ws_users, user_id) or {"kcal_target": 2100}
        eaten = sum_today_kcal(ws_meals, user_id, day)
        left = max(0, targets["kcal_target"] - eaten)

        row = daily_find_or_create(ws_daily, user_id, day)
        daily_set(ws_daily, row, 9, str(eaten))
        daily_set(ws_daily, row, 10, str(left))

        tg_send(chat_id, f"Записал ✅ ~{kcal} ккал.\nСегодня съедено: {eaten}\nОсталось: {left}", reply_markup=open_app_kb())
        return "OK", 200

    # meal text
    if text and pending == "meal":
        kcal = estimate_text_kcal(text)
        ws_meals.append_row([iso_now(), user_id, "text", "", text, "", "", str(kcal), "0.25", "", "", "MVP: текст"])
        state_clear(ws_state, user_id)

        day = today_str()
        targets = get_user_targets(ws_users, user_id) or {"kcal_target": 2100}
        eaten = sum_today_kcal(ws_meals, user_id, day)
        left = max(0, targets["kcal_target"] - eaten)

        row = daily_find_or_create(ws_daily, user_id, day)
        daily_set(ws_daily, row, 9, str(eaten))
        daily_set(ws_daily, row, 10, str(left))

        tg_send(chat_id, f"Записал ✅ ~{kcal} ккал (оценка).\nСегодня съедено: {eaten}\nОсталось: {left}", reply_markup=open_app_kb())
        return "OK", 200

    tg_send(chat_id, "Открывай мини-приложение — там основной интерфейс.", reply_markup=open_app_kb())
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
