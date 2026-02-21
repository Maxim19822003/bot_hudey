const tg = window.Telegram?.WebApp;

if (tg) {
  tg.ready();
  tg.expand();
}

const s0 = document.getElementById("s0");
const s1 = document.getElementById("s1");
const s2 = document.getElementById("s2");

const eatenEl = document.getElementById("eaten");
const leftEl = document.getElementById("left");
const stepsEl = document.getElementById("steps");

function uid() {
  return tg?.initDataUnsafe?.user?.id ? String(tg.initDataUnsafe.user.id) : "";
}

function firstName() {
  return tg?.initDataUnsafe?.user?.first_name ? String(tg.initDataUnsafe.user.first_name) : "";
}

function valOrPlaceholder(id) {
  const el = document.getElementById(id);
  if (!el) return "";
  const v = (el.value || "").trim();
  if (v) return v;
  return (el.placeholder || "").trim();
}

async function checkUserExists() {
  const id = uid();
  if (!id) return false;
  
  try {
    const res = await fetch(`/api/today?user_id=${encodeURIComponent(id)}`);
    const j = await res.json();
    // Если получили данные (даже с 0 калориями) — пользователь существует
    return j.ok === true;
  } catch (e) {
    console.error("checkUserExists error:", e);
    return false;
  }
}

async function refreshToday() {
  const id = uid();
  if (!id) {
    console.log("refreshToday: no uid");
    return;
  }
  try {
    const res = await fetch(`/api/today?user_id=${encodeURIComponent(id)}`);
    const j = await res.json();
    console.log("refreshToday response:", j);
    if (!j.ok) return;
    eatenEl.textContent = `${j.kcal_eaten} ккал`;
    leftEl.textContent = `${j.kcal_left} ккал`;
    stepsEl.textContent = `${j.steps}`;
  } catch (e) {
    console.error("refreshToday error:", e);
  }
}

// Инициализация при загрузке
async function init() {
  const exists = await checkUserExists();
  
  if (exists) {
    // Пользователь уже есть — показываем дашборд
    console.log("User exists, showing dashboard");
    s0.classList.add("hidden");
    s1.classList.add("hidden");
    s2.classList.remove("hidden");
    await refreshToday();
  } else {
    // Новый пользователь — показываем приветствие
    console.log("New user, showing welcome screen");
    s0.classList.remove("hidden");
    s1.classList.add("hidden");
    s2.classList.add("hidden");
  }
}

// Экран 0 → Экран 1
document.getElementById("go").onclick = () => {
  s0.classList.add("hidden");
  s1.classList.remove("hidden");
};

// Экран 1 → Назад
document.getElementById("back").onclick = () => {
  s1.classList.add("hidden");
  s0.classList.remove("hidden");
};

// Сохранение профиля
document.getElementById("save").onclick = async () => {
  const id = uid();
  if (!id) {
    alert("Открой мини-приложение из Telegram-кнопки бота.");
    return;
  }

  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow";
  console.log("Detected timezone:", timezone);

  const payload = {
    user_id: id,
    first_name: firstName(),
    timezone: timezone,
    start_weight_kg: valOrPlaceholder("w"),
    height_cm: valOrPlaceholder("h"),
    age: valOrPlaceholder("a"),
    goal_weight_kg: valOrPlaceholder("gw"),
    goal_weeks: valOrPlaceholder("weeks"),
    activity_level: document.getElementById("act").value,
    checkin_time: valOrPlaceholder("cin") || "08:05",
    checkout_time: valOrPlaceholder("cout") || "22:30",
  };

  console.log("Sending profile_save:", payload);

  // Минимальная проверка
  if (!payload.start_weight_kg || !payload.height_cm || !payload.age || !payload.goal_weight_kg || !payload.goal_weeks) {
    alert("Заполни: вес / рост / возраст / цель / срок.");
    return;
  }

  try {
    const res = await fetch("/api/profile_save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const j = await res.json();
    console.log("profile_save response:", j);
    
    if (!j.ok) {
      alert("Ошибка сохранения: " + (j.error || "unknown"));
      return;
    }

    // Переключаем UI на дашборд
    s1.classList.add("hidden");
    s2.classList.remove("hidden");

    await refreshToday();
  } catch (e) {
    console.error("save error:", e);
    alert("Ошибка сети. Попробуй ещё раз.");
  }
};

// Добавить еду → отправляем в бота
document.getElementById("meal").onclick = () => {
  if (!tg) return;
  tg.sendData(JSON.stringify({ action: "meal_request" }));
  tg.close();
};

// Внести вес (утро)
document.getElementById("wbtn").onclick = () => {
  if (!tg) return;
  const w = prompt("Вес утром (кг):", "");
  if (!w) return;
  tg.sendData(JSON.stringify({ action: "weight_morning", weight_morning_kg: w }));
  tg.close();
};

// Внести вес (вечер)
document.getElementById("wbtn_evening").onclick = () => {
  if (!tg) return;
  const w = prompt("Вес вечером (кг):", "");
  if (!w) return;
  tg.sendData(JSON.stringify({ action: "weight_evening", weight_evening_kg: w }));
  tg.close();
};

// Внести шаги
document.getElementById("sbtn").onclick = () => {
  if (!tg) return;
  const s = prompt("Шаги сегодня:", "");
  if (!s) return;
  tg.sendData(JSON.stringify({ action: "steps", steps: s }));
  tg.close();
};

// История веса
document.getElementById("history").onclick = async () => {
  const id = uid();
  if (!id) return;
  try {
    const res = await fetch(`/api/weight_history?user_id=${encodeURIComponent(id)}&days=30`);
    const j = await res.json();
    if (!j.ok) {
      alert("Ошибка загрузки истории");
      return;
    }
    let msg = "📊 История веса:\n\n";
    j.data.forEach(row => {
      msg += `${row.date}: ${row.morning || "?"} → ${row.evening || "?"} кг\n`;
    });
    alert(msg);
  } catch (e) {
    console.error(e);
    alert("Ошибка сети");
  }
};

// Запускаем инициализацию
init();
