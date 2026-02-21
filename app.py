import os
import json
import logging
from datetime import datetime, timezone, date

from flask import Flask, request, send_from_directory, jsonify
from flask_cors import CORS
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========= Logging =========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= ENV =========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

if not all([BOT_TOKEN, SHEET_ID, GOOGLE_CREDS_JSON]):
    raise ValueError("Missing required env vars: BOT_TOKEN, SHEET_ID, GOOGLE_CREDS_JSON")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
app = Flask(__name__)
CORS(app)  # Разрешаем запросы от WebApp

# ========= Utils =========
def iso_now():
    return datetime.now(timezone.utc).isoformat()

def today_str():
    return date.today().isoformat()

def tg_send(chat_id, text, reply_markup=None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)
        logger.info(f"tg_send: {r.status_code}")
        return r.json()
    except Exception as e:
        logger.error(f"tg_send error: {e}")
        return None

def tg_send_photo(chat_id, photo_url, caption=""):
    try:
        payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption}
        r = requests.post(f"{TELEGRAM_API}/sendPhoto", json=payload, timeout=20)
        return r.json()
    except Exception as e:
        logger.error(f"tg_send_photo error: {e}")
        return None

def tg_answer_cb(cb_id):
    try:
        requests.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": cb_id},
            timeout=10
        )
    except Exception as e:
        logger.error(f"tg_answer_cb error: {e}")

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
_sheet_client = None
_sheet_cache = {}

def get_sheet():
    global _sheet_client
    try:
        if _sheet_client is None:
            creds_dict = json.loads(GOOGLE_CREDS_JSON)
            scope = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            _sheet_client = gspread.authorize(creds)
            logger.info("Google Sheets authorized")
        return _sheet_client.open_by_key(SHEET_ID)
    except Exception as e:
        logger.error(f"get_sheet error: {e}")
        raise

def get_worksheet(name):
    """Получает лист с кэшированием и созданием при необходимости"""
    try:
        sh = get_sheet()
        try:
            return sh.worksheet(name)
        except gspread.WorksheetNotFound:
            logger.warning(f"Worksheet '{name}' not found, creating...")
            ws = sh.add_worksheet(title=name, rows=1000, cols=20)
            # Добавляем заголовки в зависимости от типа
            if name == "users":
                ws.append_row(["user_id", "first_name", "timezone", "created_at",
                              "height_cm", "age", "start_weight_kg", "goal_weight_kg",
                              "goal_deadline", "activity_level", "kcal_target", 
                              "checkin_time", "checkout_time"])
            elif name == "meals":
                ws.append_row(["ts", "user_id", "source", "meal_type", "text",
                              "photo_file_id", "photo_url", "kcal_avg", "confidence",
                              "portion", "sauce", "notes"])
            elif name == "daily_log":
                ws.append_row(["date", "user_id", "weight_morning_kg", "weight_evening_kg",
                              "steps", "workout", "water_ml", "sleep_h", "kcal_eaten",
                              "kcal_left", "mood", "untracked", "comment", "updated_at"])
            elif name == "state":
                ws.append_row(["user_id", "pending_action", "pending_since", "last_prompt"])
            return ws
    except Exception as e:
        logger.error(f"get_worksheet error: {e}")
        raise

# ========= Sheet helpers =========
def find_row_by_user(ws, user_id):
    try:
        col = ws.col_values(1)
        for i, val in enumerate(col[1:], start=2):  # skip header
            if val == str(user_id):
                return i
        return None
    except Exception as e:
        logger.error(f"find_row_by_user error: {e}")
        return None

def upsert_user(ws_users, user_id, first_name, data):
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
        logger.info(f"Updated user {user_id} at row {r}")
    else:
        ws_users.append_row(row)
        logger.info(f"Created user {user_id}")

def state_set(ws_state, user_id, pending_action, last_prompt=""):
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
    try:
        # Ищем существующую запись
        rows = ws_daily.get_all_values()
        for i in range(1, len(rows)):
            if len(rows[i]) >= 2 and rows[i][0] == day and rows[i][1] == str(user_id):
                return i + 1
        
        # Создаём новую
        ws_daily.append_row([day, user_id, "", "", "", "", "", "", "", "", "", "", "", iso_now()])
        return len(rows) + 1
    except Exception as e:
        logger.error(f"daily_find_or_create error: {e}")
        raise

def daily_set(ws_daily, row, col, value):
    try:
        ws_daily.update_cell(row, col, value)
        ws_daily.update_cell(row, 14, iso_now())  # updated_at
    except Exception as e:
        logger.error(f"daily_set error: {e}")
        raise

# ========= Math =========
def calc_kcal_target(weight_kg, height_cm, age, activity, goal_weeks):
    try:
        # Mifflin-St Jeor (муж)
        bmr = 10 * float(weight_kg) + 6.25 * float(height_cm) - 5 * float(age) + 5
        mult = {"low": 1.2, "medium": 1.375, "high": 1.55}.get(activity, 1.375)
        tdee = bmr * mult

        deficit = tdee * 0.20
        kcal_target = max(1500, int(tdee - deficit))
        steps_target = 9000 if activity != "high" else 11000

        if goal_weeks is not None and float(goal_weeks) <= 10:
            kcal_target = max(1500, int(tdee - tdee * 0.25))

        return int(tdee), int(kcal_target), int(steps_target)
    except Exception as e:
        logger.error(f"calc_kcal_target error: {e}")
        return 2500, 2100, 9000  # fallback

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
    # TODO: интеграция с AI
    return 600

# ========= Totals =========
def sum_today_kcal(ws_meals, user_id, day):
    try:
        rows = ws_meals.get_all_values()
        total = 0
        for i in range(1, len(rows)):
            r = rows[i]
            if len(r) < 8:
                continue
            uid = r[1] if len(r) > 1 else ""
            ts = r[0] if len(r) > 0 else ""
            if uid != str(user_id):
                continue
            if not ts.startswith(day):
                continue
            try:
                total += int(float(r[7] or "0"))
            except:
                pass
        return total
    except Exception as e:
        logger.error(f"sum_today_kcal error: {e}")
        return 0

def get_user_targets(ws_users, user_id):
    try:
        r = find_row_by_user(ws_users, user_id)
        if not r:
            return None
        vals = ws_users.row_values(r)
        kcal_target = 2100
        if len(vals) > 10 and vals[10]:
            try:
                kcal_target = int(float(vals[10]))
            except:
                pass
        return {"kcal_target": kcal_target}
    except Exception as e:
        logger.error(f"get_user_targets error: {e}")
        return {"kcal_target": 2100}

# ========= Web routes =========
@app.route("/", methods=["GET"])
def health():
    return "OK", 200

@app.route("/web/<path:filename>", methods=["GET"])
def web_files(filename):
    return send_from_directory("web", filename)

@app.route("/api/today", methods=["GET"])
def api_today():
    try:
        user_id = request.args.get("user_id", "").strip()
        if not user_id:
            return jsonify({"ok": False, "error": "user_id required"}), 400

        ws_users = get_worksheet("users")
        ws_meals = get_worksheet("meals")
        ws_daily = get_worksheet("daily_log")

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
            except:
                pass

        return jsonify({
            "ok": True,
            "date": day,
            "kcal_target": targets["kcal_target"],
            "kcal_eaten": eaten,
            "kcal_left": left,
            "steps": steps
        })
    except Exception as e:
        logger.error(f"api_today error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/profile_save", methods=["POST"])
def api_profile_save():
    try:
        data = request.get_json(force=True) or {}
        logger.info(f"profile_save called with: {data}")

        user_id = str(data.get("user_id", "")).strip()
        first_name = str(data.get("first_name", "")).strip() or "user"

        if not user_id:
            return jsonify({"ok": False, "error": "user_id required"}), 400

        ws_users = get_worksheet("users")

        def fnum(x, default=None):
            try:
                s = str(x).replace(",", ".").strip()
                return float(s)
            except:
                return default

        def inum(x, default=None):
            try:
                return int(float(str(x).replace(",", ".").strip()))
            except:
                return default

        start_w = fnum(data.get("start_weight_kg"))
        height = fnum(data.get("height_cm"))
        age = inum(data.get("age"))
        goal_w = fnum(data.get("goal_weight_kg"))
        goal_weeks = fnum(data.get("goal_weeks"))

        activity = (data.get("activity_level") or "medium").strip()
        timezone_name = (data.get("timezone") or "Europe/Moscow").strip()
        checkin_time = (data.get("checkin_time") or "08:05").strip()
        checkout_time = (data.get("checkout_time") or "22:30").strip()

        tdee, kcal_target, steps_target = 0, 2100, 9000
        if start_w and height and age:
            tdee, kcal_target, steps_target = calc_kcal_target(start_w, height, age, activity, goal_weeks)

        payload = {
            "timezone": timezone_name,
            "created_at": iso_now(),
            "height_cm": height or "",
            "age": age or "",
            "start_weight_kg": start_w or "",
            "goal_weight_kg": goal_w or "",
            "goal_deadline": f"{int(goal_weeks)} недель" if goal_weeks else "",
            "activity_level": activity,
            "kcal_target": kcal_target,
            "checkin_time": checkin_time,
            "checkout_time": checkout_time,
        }

        upsert_user(ws_users, user_id, first_name, payload)

        return jsonify({
            "ok": True,
            "kcal_target": kcal_target,
            "steps_target": steps_target
        })
    except Exception as e:
        logger.error(f"api_profile_save error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ========= Telegram webhook =========
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if WEBHOOK_SECRET and request.args.get("secret", "") != WEBHOOK_SECRET:
            return "Forbidden", 403

        update = request.get_json(force=True)
        logger.info(f"webhook update: {update}")

        # callbacks
        if "callback_query" in update:
            q = update["callback_query"]
            tg_answer_cb(q["id"])
            chat_id = q["message"]["chat"]["id"]
            user_id = str(q.get("from", {}).get("id", ""))
            data = q.get("data", "")

            if data == "meal_prompt":
                ws_state = get_worksheet("state")
                state_set(ws_state, user_id, "meal", "Ждём фото или текст еды")
                tg_send(chat_id, "Кидай фото еды 📸\nЕсли фото не получается — напиши текстом, что съел.", reply_markup=cancel_kb())
                return "OK", 200

            if data == "cancel":
                ws_state = get_worksheet("state")
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
            except Exception as e:
                logger.error(f"web_app_data parse error: {e}")
                tg_send(chat_id, "Не понял данные из приложения.", reply_markup=open_app_kb())
                return "OK", 200

            action = payload.get("action", "")
            
            if action == "weight_morning":
                ws_daily = get_worksheet("daily_log")
                w = str(payload.get("weight_morning_kg", "")).strip()
                day = today_str()
                row = daily_find_or_create(ws_daily, user_id, day)
                daily_set(ws_daily, row, 3, w)
                tg_send(chat_id, f"Вес записал ✅ {w} кг", reply_markup=open_app_kb())
                return "OK", 200

                    if action == "weight_evening":
            ws_daily = get_worksheet("daily_log")
            w = str(payload.get("weight_evening_kg", "")).strip()
            day = today_str()
            row = daily_find_or_create(ws_daily, user_id, day)
            daily_set(ws_daily, row, 4, w)  # колонка D = weight_evening_kg
            tg_send(chat_id, f"Вечерний вес записал ✅ {w} кг", reply_markup=open_app_kb())
            return "OK", 200

            if action == "steps":
                ws_daily = get_worksheet("daily_log")
                s = str(payload.get("steps", "")).strip()
                day = today_str()
                row = daily_find_or_create(ws_daily, user_id, day)
                daily_set(ws_daily, row, 5, s)
                tg_send(chat_id, f"Шаги записал ✅ {s}", reply_markup=open_app_kb())
                return "OK", 200

            tg_send(chat_id, "Ок.", reply_markup=open_app_kb())
            return "OK", 200

        # State-based handlers
        ws_state = get_worksheet("state")
        pending = state_get(ws_state, user_id)

        # meal photo
        if "photo" in msg and pending == "meal":
            ws_meals = get_worksheet("meals")
            ws_daily = get_worksheet("daily_log")
            ws_users = get_worksheet("users")
            
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
            ws_meals = get_worksheet("meals")
            ws_daily = get_worksheet("daily_log")
            ws_users = get_worksheet("users")
            
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
        
    except Exception as e:
        logger.error(f"webhook error: {e}")
        return "Error", 500

@app.route("/api/weight_history", methods=["GET"])
def api_weight_history():
    try:
        user_id = request.args.get("user_id", "").strip()
        days = int(request.args.get("days", "30"))
        
        if not user_id:
            return jsonify({"ok": False, "error": "user_id required"}), 400

        ws_daily = get_worksheet("daily_log")
        rows = ws_daily.get_all_values()
        
        data = []
        for i in range(1, len(rows)):
            r = rows[i]
            if len(r) < 3:
                continue
            date_val = r[0]
            uid = r[1]
            if uid != str(user_id):
                continue
            
            morning = r[2] if len(r) > 2 else ""
            evening = r[3] if len(r) > 3 else ""
            
            data.append({
                "date": date_val,
                "morning": morning,
                "evening": evening
            })
        
        # Сортируем по дате, берём последние N дней
        data = sorted(data, key=lambda x: x["date"], reverse=True)[:days]
        
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"api_weight_history error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
