import os
import json
from datetime import datetime, timezone
from flask import Flask, request
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========= ENV =========
BOT_TOKEN = os.environ["BOT_TOKEN"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)

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

def ensure_headers(sh):
    required = {
        "users": ["user_id","first_name","timezone","created_at"],
        "meals": ["ts","user_id","meal_type","text","photo_file_id","photo_url","kcal_avg","confidence","notes"],
        "daily_log": ["date","user_id","weight_kg","steps","workout","water_ml","sleep_h","comment","updated_at"],
    }

    existing_titles = [ws.title for ws in sh.worksheets()]

    for title, headers in required.items():
        if title not in existing_titles:
            ws = sh.add_worksheet(title=title, rows=1000, cols=20)
            ws.append_row(headers)
        else:
            ws = sh.worksheet(title)
            if ws.row_values(1) != headers:
                ws.delete_rows(1)
                ws.insert_row(headers, 1)

# ========= Telegram helpers =========
def tg_send(chat_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)

def tg_send_video(chat_id: int, caption: str, reply_markup=None):
    with open("gipsy.mp4", "rb") as video:
        files = {"video": video}
        data = {"chat_id": chat_id, "caption": caption}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        requests.post(f"{TELEGRAM_API}/sendVideo", data=data, files=files)

def tg_get_file_url(file_id: str) -> str:
    r = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    file_path = r["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

def main_menu():
    return {
        "inline_keyboard": [
            [{"text": "🔥 НАЧАТЬ", "callback_data": "begin"}],
            [
                {"text": "⚖️ Вес", "callback_data": "weight"},
                {"text": "📸 Еда", "callback_data": "meal"}
            ],
            [
                {"text": "🚶 Шаги", "callback_data": "steps"},
                {"text": "📊 Итог дня", "callback_data": "summary"}
            ]
        ]
    }

# ========= Calorie Estimator =========
def estimate_kcal_avg(photo_url: str):
    return 600, 0.35, "MVP: без распознавания (заглушка)"

# ========= Routes =========
@app.route("/", methods=["GET"])
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        if request.args.get("secret", "") != WEBHOOK_SECRET:
            return "Forbidden", 403

    update = request.get_json(force=True)

    # Обработка кнопок
    if "callback_query" in update:
        query = update["callback_query"]
        chat_id = query["message"]["chat"]["id"]
        data = query["data"]

        if data == "begin":
            tg_send(chat_id, "Сделка принята. Начинаем с цифр.\nПришли вес.")
        elif data == "weight":
            tg_send(chat_id, "Введи текущий вес (кг).")
        elif data == "meal":
            tg_send(chat_id, "Пришли фото еды.")
        elif data == "steps":
            tg_send(chat_id, "Сколько шагов сегодня?")
        elif data == "summary":
            tg_send(chat_id, "Итог дня скоро будет здесь.")
        return "OK", 200

    msg = update.get("message")
    if not msg:
        return "OK", 200

    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    user_id = str(user.get("id", ""))
    first_name = user.get("first_name", "")

    sh = get_sheet()
    ensure_headers(sh)
    ws_users = sh.worksheet("users")
    ws_meals = sh.worksheet("meals")

    text = msg.get("text", "")

    # /start
    if text == "/start":
        existing_ids = ws_users.col_values(1)
        if user_id not in existing_ids:
            ws_users.append_row([user_id, first_name, "Europe/Amsterdam", datetime.utcnow().isoformat()])

        tg_send_video(
            chat_id,
            "БОТ ХУДЕЙ 🕯️\nСделка принята.\nДальше — цифры.",
            reply_markup=main_menu()
        )
        return "OK", 200

    # Фото еды
    if "photo" in msg:
        best = msg["photo"][-1]
        file_id = best["file_id"]
        photo_url = tg_get_file_url(file_id)

        kcal_avg, conf, notes = estimate_kcal_avg(photo_url)
        ts = datetime.now(timezone.utc).isoformat()

        ws_meals.append_row([
            ts, user_id, "", "", file_id, photo_url,
            str(kcal_avg), str(conf), notes
        ])

        tg_send(chat_id, f"Записал ✅\nПо фото в среднем: ~{kcal_avg} ккал.", reply_markup=main_menu())
        return "OK", 200

    # Вес (просто число)
    if text and text.replace(".", "", 1).isdigit():
        tg_send(chat_id, f"Вес {text} кг записан.", reply_markup=main_menu())
        return "OK", 200

    tg_send(chat_id, "Выбери действие ниже.", reply_markup=main_menu())
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
