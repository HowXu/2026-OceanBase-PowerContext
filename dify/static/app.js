"use strict";
const $ = (id) => document.getElementById(id);
let player = localStorage.getItem("station7_player") || crypto.randomUUID().replaceAll("-", "");
localStorage.setItem("station7_player", player);
let pending = null;
let busy = false;
const opening = $("messages").innerHTML;

function identity() {
  $("passenger").textContent = "旅客 " + player.slice(0, 6);
  $("save-id").value = player;
}
identity();

function addMessage(text, isPlayer) {
  const article = document.createElement("article");
  article.className = "message " + (isPlayer ? "player" : "station");
  if (!isPlayer) {
    const avatar = document.createElement("span");
    avatar.className = "avatar"; avatar.textContent = "七"; article.append(avatar);
  }
  const body = document.createElement("div");
  const speaker = document.createElement("span"); speaker.className = "speaker";
  speaker.textContent = isPlayer ? "你" : "站长老七";
  const p = document.createElement("p"); p.textContent = text;
  body.append(speaker, p); article.append(body); $("messages").append(article);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function renderState(state, result) {
  $("trust").textContent = state.trust;
  $("trust-bar").style.width = Math.max(0, Math.min(100, state.trust * 20)) + "%";
  $("count").textContent = state.items.length;
  $("items").replaceChildren();
  if (!state.items.length) {
    const li = document.createElement("li"); li.className = "empty-item";
    li.textContent = "还没有寄存的失物。第一件，会是什么呢？"; $("items").append(li);
  }
  state.items.forEach((item) => { const li = document.createElement("li"); li.textContent = item; $("items").append(li); });
  $("ending").hidden = !state.ending;
  $("ending").textContent = "到站纪念 · " + state.ending;
  $("session-label").textContent = "第 " + state.turn + " 次到站";
  $("evidence").textContent = "PowerContext\n" + result.memory_ref + "\n\nDify 流程\n" + result.workflow_run_id + "\n\n旁白模式\n" + result.narrator;
}

async function send(retry = false) {
  if (busy) return;
  const action = $("action").value.trim();
  if (!retry && !action) return;
  if (!retry) {
    pending = { player_id: player, action, request_id: crypto.randomUUID() };
    addMessage(action, true);
    $("action").value = "";
  }
  busy = true; $("send").disabled = true; $("action").disabled = true;
  $("new-session").disabled = true;
  $("save-toggle").disabled = true;
  $("save-form").querySelector("button").disabled = true;
  $("error").hidden = true; $("retry").hidden = true;
  $("input-hint").textContent = "站长正在翻登记簿，等一等……";
  try {
    const response = await fetch("/api/play", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(pending) });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "输入未通过校验，请检查存档口令和行动长度。");
    addMessage(result.answer, false); renderState(result.state, result); pending = null;
  } catch (error) {
    $("error").textContent = error.message || "暂时未能联系上车站，请重试。";
    $("error").hidden = false; $("retry").hidden = false;
  } finally {
    busy = false; $("send").disabled = false; $("action").disabled = false;
    $("new-session").disabled = false; $("save-toggle").disabled = false;
    $("save-form").querySelector("button").disabled = false;
    $("input-hint").textContent = "你的选择，会被这座车站记住。";
  }
}
$("play-form").addEventListener("submit", (event) => { event.preventDefault(); send(); });
$("retry").addEventListener("click", () => send(true));
$("action").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {event.preventDefault(); send();} });
document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => { if (busy) return; $("action").value = button.dataset.action; $("action").focus(); }));
$("new-session").addEventListener("click", () => {
  if (busy) return;
  $("messages").innerHTML = opening;
  $("session-label").textContent = "换了车厢，站长还认得你";
  $("error").hidden = true; $("retry").hidden = true; pending = null;
  $("action").value = "你还记得我上次带来的失物吗？"; $("action").focus();
});
$("save-toggle").addEventListener("click", () => { $("save-form").hidden = !$("save-form").hidden; });
$("save-mobile").addEventListener("click", () => { document.body.classList.toggle("show-passport"); $("save-form").hidden = false; });
$("save-form").addEventListener("submit", (event) => {
  event.preventDefault(); if (busy) return;
  player = $("save-id").value.trim(); localStorage.setItem("station7_player", player); identity();
  $("messages").innerHTML = opening; $("save-form").hidden = true;
  document.body.classList.remove("show-passport");
  $("error").hidden = true; $("retry").hidden = true; pending = null;
  renderState({trust:0,items:[],ending:"",turn:0}, {memory_ref:"尚未读取此存档", workflow_run_id:"—", narrator:"—"});
  $("action").value = "你还记得我上次带来的失物吗？";
});
fetch("/health").then((r) => r.json()).then((data) => {
  $("service-status").textContent = data.dify_configured ? "车站已连接" : "等待配置 Dify 应用";
}).catch(() => { $("service-status").textContent = "车站暂时离线，请检查服务"; });
