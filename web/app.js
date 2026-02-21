const tg = window.Telegram?.WebApp;

if (tg){
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

function valOrPlaceholder(id){
  const el = document.getElementById(id);
  const v = (el.value || "").trim();
  if (v) return v;
  return (el.placeholder || "").trim();
}

async function refreshToday() {
  const id = uid();
  if (!id) return;
  const res = await fetch(`/api/today?user_id=${encodeURIComponent(id)}`);
  const j = await res.json();
  if (!j.ok) return;
  eatenEl.textContent = `${j.kcal_eaten} ккал`;
  leftEl.textContent = `${j.kcal_left} ккал`;
  stepsEl.textContent = `${j.steps}`;
}

document.getElementById("go").onclick = () => {
  s0.classList.add("hidden");
  s1.classList.remove("hidden");
};

document.getElementById("back").onclick = () => {
  s1.classList.add("hidden");
  s0.classList.remove("hidden");
};

document.getElementById("save").onclick = () => {
  if (!tg){
    alert("Открой мини-приложение из Telegram-кнопки бота.");
    return;
  }

  const payload = {
    action: "profile_save",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow",

    start_weight_kg: valOrPlaceholder("w"),
    height_cm: valOrPlaceholder("h"),
    age: valOrPlaceholder("a"),
    goal_weight_kg: valOrPlaceholder("gw"),
    goal_weeks: valOrPlaceholder("weeks"),

    activity_level: document.getElementById("act").value,
    checkin_time: (valOrPlaceholder("cin") || "08:05"),
    checkout_time: (valOrPlaceholder("cout") || "22:30"),
  };

  // мини-валидация
  if (!payload.start_weight_kg || !payload.height_cm || !payload.age || !payload.goal_weight_kg || !payload.goal_weeks){
    alert("Заполни вес/рост/возраст/цель/срок.");
    return;
  }

  tg.sendData(JSON.stringify(payload));

  // UI переключаем, но реальная запись — на стороне бота
  s1.classList.add("hidden");
  s2.classList.remove("hidden");
  refreshToday();
};

document.getElementById("meal").onclick = () => {
  if (!tg) return;
  tg.sendData(JSON.stringify({ action: "meal_request" }));
  tg.close();
};

document.getElementById("wbtn").onclick = () => {
  if (!tg) return;
  const w = prompt("Вес утром (кг):", "");
  if (!w) return;
  tg.sendData(JSON.stringify({ action: "weight_morning", weight_morning_kg: w }));
};

document.getElementById("sbtn").onclick = () => {
  if (!tg) return;
  const s = prompt("Шаги сегодня:", "");
  if (!s) return;
  tg.sendData(JSON.stringify({ action: "steps", steps: s }));
};

refreshToday();
