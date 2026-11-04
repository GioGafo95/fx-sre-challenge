const $ = id => document.getElementById(id);
const fmtDate = iso => new Date(iso + "T12:00:00").toLocaleDateString(undefined, {weekday:"short", day:"numeric", month:"short", year:"numeric"});
const fmtRate = s => s === null ? "n/a" : Number(s).toFixed(4);

function spark(history){
  const pts = history.slice().reverse().map(h => Number(h.rate));
  const svg = $("spark");
  if (pts.length < 2){ svg.hidden = true; return; }
  svg.hidden = false;
  const min = Math.min(...pts), max = Math.max(...pts), span = (max - min) || 1;
  const W = 600, H = 80, pad = 6;
  const xy = pts.map((v,i) => [i * W / (pts.length - 1), pad + (H - 2*pad) * (1 - (v - min) / span)]);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `<path d="M${xy.map(p => p.join(",")).join(" L")}"/>`;
}

async function load(){
  $("loading").hidden = false; $("error").hidden = true; $("content").hidden = true;
  try {
    const r = await fetch("/api/rates", {headers:{Accept:"application/json"}});
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    $("rate").textContent = fmtRate(d.current.rate);
    $("asof").textContent = "Published " + fmtDate(d.current.date) + (d.source === "demo" ? " (demo data)" : "");
    $("stale").hidden = !d.stale;
    $("avg15").textContent = fmtRate(d.average.last_15_days);
    $("avg30").textContent = fmtRate(d.average.last_30_days);
    $("rows").replaceChildren(...d.history.map(h => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${fmtDate(h.date)}</td><td>${fmtRate(h.rate)}</td>`;
      return tr;
    }));
    spark(d.history);
    document.title = `1 USD = ${fmtRate(d.current.rate)} MXN`;
    $("content").hidden = false;
  } catch (e) {
    $("error").hidden = false;
  } finally {
    $("loading").hidden = true;
  }
}
$("retry").addEventListener("click", load);
load();
