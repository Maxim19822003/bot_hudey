const tg = window.Telegram.WebApp;
tg.expand();

const s0 = document.getElementById("s0");
const s1 = document.getElementById("s1");
const s2 = document.getElementById("s2");

const eatenEl = document.getElementById("eaten");
const leftEl = document.getElementById("left");
const stepsEl = document.getElementById("steps");

function uid() {
  // Telegram WebApp user id доступен в initDataUnsafe
  return tg.initDataUnsafe?.user?.id ? String(tg.initDataUnsafe.user.id) : "";
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
  const payload = {
    action: "profile_save",
    timezone: "Europe/Moscow",
    start_weight_kg: document.getElementById("w").value.trim(),
    height_cm: document.getElementById("h").value.trim(),
    age: document.getElementById("a").value.trim(),
    goal_weight_kg: document.getElementById("gw").value.trim(),
    goal_weeks: document.getElementById("weeks").value.trim(),
    activity_level: document.getElementById("act").value,
    checkin_time: document.getElementById("cin").value.trim() || "08:05",
    checkout_time: document.getElementById("cout").value.trim() || "22:30",
  };

  tg.sendData(JSON.stringify(payload));

  s1.classList.add("hidden");
  s2.classList.remove("hidden");
  refreshToday();
};

document.getElementById("meal").onclick = () => {
  tg.sendData(JSON.stringify({ action: "meal_request" }));
  tg.close(); // переводим в чат, где бот попросит фото/текст
};

document.getElementById("wbtn").onclick = () => {
  const w = prompt("Вес утром (кг):", "");
  if (!w) return;
  tg.sendData(JSON.stringify({ action: "weight_morning", weight_morning_kg: w }));
};

document.getElementById("sbtn").onclick = () => {
  const s = prompt("Шаги сегодня:", "");
  if (!s) return;
  tg.sendData(JSON.stringify({ action: "steps", steps: s }));
};

refreshToday();
