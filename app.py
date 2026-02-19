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
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]  # JSON целиком одной строкой
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
    # Создаёт листы/заголовки, если их нет (удобно, чтобы не ошибиться)
    required = {
        "users": ["user_id","first_name","timezone","created_at"],
        "meals": ["ts","user_id","meal_type","text","photo_file_id","photo_url","kcal_avg","confidence","notes"],
        "daily_log": ["date","user_id","weight_kg","steps","workout","water_ml","sleep_h","comment","updated_at"],
    }
    existing_titles = [ws.title for ws in sh.worksheets()]

    for title, headers in required.items():
        if title not in existing_titles:
            ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers) + 5)
            ws.append_row(headers)
        else:
            ws = sh.worksheet(title)
            row1 = ws.row_values(1)
            if row1 != headers:
                # если пусто/не совпадает — ставим как надо
                if not row1:
                    ws.append_row(headers)
                else:
                    ws.delete_rows(1)
                    ws.insert_row(headers, 1)

# ========= Telegram helpers =========
def tg_send(chat_id: int, text: str):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})

def tg_get_file_url(file_id: str) -> str:
    r = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    file_path = r["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

# ========= MVP calorie estimator (заглушка) =========
def estimate_kcal_avg(photo_url: str) -> tuple[int, float, str]:
    # Пока без реального распознавания: средняя оценка “по умолчанию”
    # Потом заменим на AI по фото.
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
        tg_send(chat_id, "БОТ ХУДЕЙ 🕯️\nКидай фото еды — я запишу и дам среднюю оценку калорий.")
        return "OK", 200

    # Фото еды
    if "photo" in msg:
        best = msg["photo"][-1]
        file_id = best["file_id"]
        photo_url = tg_get_file_url(file_id)

        kcal_avg, conf, notes = estimate_kcal_avg(photo_url)

        ts = datetime.now(timezone.utc).isoformat()
        ws_meals.append_row([
            ts, user_id, "", "", file_id, photo_url, str(kcal_avg), str(conf), notes
        ])

        tg_send(chat_id, f"Записал ✅\nПо фото в среднем: ~{kcal_avg} ккал.")
        return "OK", 200

    # Любой текст
    if text:
        tg_send(chat_id, "Понял. Для MVP: отправь фото еды или /start.")
        return "OK", 200

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
