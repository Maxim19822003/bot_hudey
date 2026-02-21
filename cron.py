# cron.py
import os
import json
import logging
from datetime import datetime, timezone, timedelta

import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= ENV =========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON")

if not all([BOT_TOKEN, SHEET_ID, GOOGLE_CREDS_JSON]):
    raise ValueError("Missing required env vars")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

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

def get_worksheet(name):
    sh = get_sheet()
    return sh.worksheet(name)

# ========= Telegram =========
def tg_send(chat_id, text, reply_markup=None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)
        logger.info(f"Sent to {chat_id}: {r.status_code}")
        return r.json()
    except Exception as e:
        logger.error(f"tg_send error: {e}")
        return None

def open_app_kb():
    webapp_url = f"{os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')}/web/index.html"
    return {
        "inline_keyboard": [
            [{"text": "🔥 Открыть мини-приложение", "web_app": {"url": webapp_url}}]
        ]
    }

# ========= Time helpers =========
def now_in_timezone(tz_name):
    """Получаем текущее время в часовом поясе пользователя"""
    from zoneinfo import ZoneInfo
    try:
        return datetime.now(ZoneInfo(tz_name))
    except:
        return datetime.now(ZoneInfo("Europe/Moscow"))

def time_matches(check_time_str, current_dt, tolerance_minutes=5):
    """Проверяем, совпадает ли текущее время с check_time_str (HH:MM)"""
    try:
        hour, minute = map(int, check_time_str.split(":"))
        scheduled = current_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        diff = abs((current_dt - scheduled).total_seconds())
        return diff <= tolerance_minutes * 60
    except:
        return False

# ========= Main logic =========
def run_checkin():
    """Утренний чек-ин: взвесься"""
    logger.info("Running checkin reminder...")
    ws_users = get_worksheet("users")
    rows = ws_users.get_all_values()
    
    for i in range(1, len(rows)):
        r = rows[i]
        if len(r) < 13:
            continue
        
        user_id = r[0]
        timezone = r[2] or "Europe/Moscow"
        checkin_time = r[11] or "08:05"
        
        now = now_in_timezone(timezone)
        
        if time_matches(checkin_time, now):
            tg_send(
                user_id,
                "🌅 Доброе утро! Время взвеситься.\n\nСтарик следит за тобой...",
                reply_markup=open_app_kb()
            )

def run_checkout():
    """Вечерний отчёт: шаги, активность, вес"""
    logger.info("Running checkout reminder...")
    ws_users = get_worksheet("users")
    ws_daily = get_worksheet("daily_log")
    
    from datetime import date
    today = date.today().isoformat()
    
    rows = ws_users.get_all_values()
    
    for i in range(1, len(rows)):
        r = rows[i]
        if len(r) < 13:
            continue
        
        user_id = r[0]
        first_name = r[1]
        timezone = r[2] or "Europe/Moscow"
        checkout_time = r[12] or "22:30"
        kcal_target = r[10] or "2100"
        
        now = now_in_timezone(timezone)
        
        if time_matches(checkout_time, now):
            # Получаем данные за сегодня
            daily_rows = ws_daily.get_all_values()
            today_data = None
            for dr in daily_rows[1:]:
                if len(dr) >= 2 and dr[0] == today and dr[1] == user_id:
                    today_data = dr
                    break
            
            morning = today_data[2] if today_data and len(today_data) > 2 else "?"
            evening = today_data[3] if today_data and len(today_data) > 3 else "?"
            steps = today_data[4] if today_data and len(today_data) > 4 else "0"
            kcal_eaten = today_data[8] if today_data and len(today_data) > 8 else "0"
            
            left = int(kcal_target) - int(kcal_eaten or 0)
            
            msg = f"""🌙 Вечерний отчёт, {first_name}

⚖️ Вес: {morning} → {evening} кг
🚶 Шаги: {steps}
🍽 Съедено: {kcal_eaten} / {kcal_target} ккал
📊 Осталось: {left} ккал

Старик доволен?"""
            
            tg_send(user_id, msg, reply_markup=open_app_kb())

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python cron.py [checkin|checkout]")
        sys.exit(1)
    
    mode = sys.argv[1]
    if mode == "checkin":
        run_checkin()
    elif mode == "checkout":
        run_checkout()
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
