#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
评估结果前后对比报告（单文件 HTML）

用法：
    python report.py                 # 载入 results/ 下全部轮次，默认对比最近两轮
    python report.py --open          # 生成后自动用浏览器打开
    python report.py a.csv b.csv     # 只放这两轮进来
    python report.py -o other.html

所有轮次都内嵌在一个 html 里，页面顶部用下拉框随时切换"对比基准"和"当前"，
不用重新生成、不用记文件名。零外部依赖、可离线、可直接放 GitHub Pages。
"""
import argparse
import collections
import csv
import datetime
import glob
import io
import json
import os
import re
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

CAT_NAME = {
    "oral": "口语",
    "term": "术语",
    "multi": "多跳",
    "noans": "无答案",
    "exact": "精确型",
    "syn": "同义变体",
    "cond": "带条件",
}
CAT_ORDER = ["oral", "term", "multi", "syn", "exact", "cond", "noans"]


def load_round(path):
    """读一轮 CSV，返回 {items: [..], ...}，读不动返回 None"""
    try:
        rows = list(csv.DictReader(io.open(path, encoding="utf-8-sig")))
    except (IOError, OSError):
        return None
    items = []
    for r in rows:
        try:
            items.append({
                "id": int(r["id"]),
                "cat": r.get("cat", ""),
                "q": r.get("q", ""),
                "w3": float(r.get("w3") or 0),
                "top1": float(r.get("top1") or 0),
                "hit": int(float(r.get("hit") or 0)),
                "seg": int(float(r.get("segments") or 0)),
                "diff": r.get("difficulty", ""),
                "flaky": int(float(r.get("flaky") or 0)),
            })
        except (ValueError, KeyError):
            continue
    if not items:
        return None
    return {"name": os.path.basename(path).replace(".csv", ""), "items": items}


def pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def summarize(rnd):
    items = rnd["items"]
    total = len(items)
    hit = sum(i["hit"] for i in items)
    stable = [i for i in items if not i["flaky"]]
    cats = collections.OrderedDict()
    for c in CAT_ORDER:
        sub = [i for i in items if i["cat"] == c]
        if sub:
            cats[c] = {"n": len(sub), "hit": sum(i["hit"] for i in sub),
                       "rate": pct(sum(i["hit"] for i in sub), len(sub))}
    return {
        "total": total, "hit": hit, "rate": pct(hit, total),
        "stable_total": len(stable),
        "stable_rate": pct(sum(i["hit"] for i in stable), len(stable)),
        "cats": cats,
    }


def friendly(name):
    """20261106-130559-v2-70q -> 11-06 13:05 · v2-70q"""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}-?(.*)$", name)
    if not m:
        return name
    _, mo, d, hh, mm, tag = m.groups()
    return "%s-%s %s:%s · %s" % (mo, d, hh, mm, tag or "-")


def build_html(rounds):
    payload = json.dumps({
        "rounds": rounds,
        "catName": CAT_NAME,
        "catOrder": CAT_ORDER,
        "built": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA__", payload, 1)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAG 检索评估 · 前后对比</title>
<style>
:root{
  --bg:#FBFAF7;--card:#FFF;--line:#E5E2DA;--t1:#1F1E1B;--t2:#5F5E5A;--t3:#8C8A83;
  --good:#1D9E75;--bad:#C0492A;--gray:#888780;
  --good-bg:#E1F5EE;--bad-bg:#FAECE7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--t1);padding:28px 20px 80px;
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:980px;margin:0 auto}
h1{font-size:21px;font-weight:600;margin:0 0 3px}
.sub{color:var(--t3);font-size:12px;margin-bottom:20px}
.picker{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:14px 18px;margin-bottom:16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.picker label{font-size:12px;color:var(--t3)}
select{font:inherit;font-size:13px;padding:7px 10px;border:1px solid var(--line);
  border-radius:8px;background:#fff;color:var(--t1);max-width:290px;cursor:pointer}
select:focus{outline:2px solid #CFCBC2;outline-offset:1px}
.swap{border:1px solid var(--line);background:#fff;border-radius:8px;padding:7px 12px;
  font-size:13px;cursor:pointer;color:var(--t2)}
.swap:hover{border-color:#C9C5BA}
.picker .note{margin-left:auto;font-size:12px;color:var(--t3)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:16px}
h2{font-size:15px;font-weight:600;margin:0 0 3px}
.hint{color:var(--t3);font-size:12px;margin:0 0 16px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px 17px}
.kpi .k{font-size:12px;color:var(--t3);margin-bottom:5px}
.kpi .v{font-size:25px;font-weight:600;letter-spacing:-.5px}
.kpi .d{font-size:12px;margin-top:3px}
.up{color:var(--good)}.down{color:var(--bad)}.flat{color:var(--t3)}
.bar-row{display:grid;grid-template-columns:92px 1fr 138px;align-items:center;gap:12px;margin-bottom:11px}
.bar-name{font-size:13px;color:var(--t2)}
.bar-val{font-size:12px;text-align:right;color:var(--t2);font-variant-numeric:tabular-nums}
.pair{display:flex;flex-direction:column;gap:3px}
.pair .tr{position:relative;height:8px;background:#F3F1EC;border-radius:3px;overflow:hidden}
.pair .fl{position:absolute;left:0;top:0;height:100%;border-radius:3px}
.legend{display:flex;gap:16px;font-size:12px;color:var(--t3);margin-bottom:14px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.chip{border:1px solid var(--line);background:#fff;border-radius:20px;padding:5px 13px;
  font-size:12px;cursor:pointer;color:var(--t2)}
.chip:hover{border-color:#C9C5BA}
.chip.on{background:var(--t1);border-color:var(--t1);color:#fff}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:500;color:var(--t3);font-size:12px;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:8px 10px;border-bottom:1px solid #F2F0EB;vertical-align:top}
tr:hover td{background:#FCFBF8}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;font-size:11px;padding:2px 7px;border-radius:10px;
  background:#F3F1EC;color:var(--t2);white-space:nowrap}
.tag.hit{background:var(--good-bg);color:#0F6E56}
.tag.miss{background:var(--bad-bg);color:#993C1D}
footer{text-align:center;color:var(--t3);font-size:12px;margin-top:26px}
@media(max-width:760px){.kpis{grid-template-columns:repeat(2,1fr)}
  .bar-row{grid-template-columns:70px 1fr 104px}.picker .note{display:none}}
</style>
</head>
<body>
<div class="wrap">
  <h1>RAG 检索评估 · 前后对比</h1>
  <div class="sub" id="sub"></div>

  <div class="picker">
    <label for="selA">对比基准</label>
    <select id="selA"></select>
    <label for="selB">当前</label>
    <select id="selB"></select>
    <button class="swap" id="swapBtn">交换</button>
    <span class="note" id="note"></span>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="card">
    <h2>分类命中率</h2>
    <p class="hint">上行灰色＝对比基准，下行彩色＝当前。样本少于 5 题的类别结论不可靠。</p>
    <div class="legend">
      <span><i style="background:#CFCBC2"></i>基准</span>
      <span><i style="background:#1D9E75"></i>当前（变差为红，无变化为浅绿）</span>
    </div>
    <div id="cats"></div>
  </div>

  <div class="card">
    <h2>逐题变化散点</h2>
    <p class="hint">横轴基准分数，纵轴当前分数。落虚线＝没变；越往左上提升越大，右下退步。</p>
    <div id="scatter"></div>
    <div class="hint" id="scatterNote" style="margin:10px 0 0"></div>
  </div>

  <div class="card">
    <h2>提升与退步最大的题</h2>
    <p class="hint">按前三段加权分（w3）排序，阈值 ±0.02（与 run_eval.py --diff 一致）。</p>
    <div id="movers"></div>
  </div>

  <div class="card">
    <h2>全部题目</h2>
    <div class="tools" id="filters"></div>
    <table>
      <thead><tr>
        <th style="width:44px">#</th><th style="width:78px">类别</th><th>问题</th>
        <th class="num" style="width:66px">基准</th><th class="num" style="width:66px">当前</th>
        <th class="num" style="width:64px">变化</th><th style="width:72px">结果</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

  <footer id="foot"></footer>
</div>

<script>
const D = __DATA__;
const R = D.rounds;
const fmt = (x,n=3) => x == null ? "—" : Number(x).toFixed(n);
const sgn = x => (x > 0 ? "+" : "") + x.toFixed(3);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let iA = Math.max(0, R.length - 2), iB = R.length - 1, filter = "all", cur = null;

function label(r){ return r.label; }

function pair(){
  const A = R[iA], B = R[iB];
  const ma = new Map(A.items.map(i => [i.id, i]));
  const mb = new Map(B.items.map(i => [i.id, i]));
  const ids = [...new Set([...ma.keys(), ...mb.keys()])].sort((x,y) => x-y);
  return ids.map(id => {
    const a = ma.get(id), b = mb.get(id), src = b || a;
    return {
      id, cat: src.cat, q: src.q, seg: src.seg,
      w3a: a ? a.w3 : null, w3b: b ? b.w3 : null,
      hita: a ? a.hit : null, hitb: b ? b.hit : null
    };
  });
}

function renderKpis(){
  const A = R[iA], B = R[iB], items = cur;
  const d = +(B.sums.rate - A.sums.rate).toFixed(1);
  const ds = +(B.sums.stable_rate - A.sums.stable_rate).toFixed(1);
  const better = items.filter(i => i.w3a != null && i.w3b != null && i.w3b - i.w3a > 0.02).length;
  const worse  = items.filter(i => i.w3a != null && i.w3b != null && i.w3a - i.w3b > 0.02).length;
  const cls = v => v > 0 ? "up" : v < 0 ? "down" : "flat";
  document.getElementById("kpis").innerHTML = `
    <div class="kpi"><div class="k">总命中率</div><div class="v">${B.sums.rate.toFixed(1)}%</div>
      <div class="d ${cls(d)}">${(d>=0?"↑ ":"↓ ")+Math.abs(d)} pt　基准 ${A.sums.rate.toFixed(1)}%</div></div>
    <div class="kpi"><div class="k">通过题数</div><div class="v">${B.sums.hit}/${B.sums.total}</div>
      <div class="d flat">基准 ${A.sums.hit}/${A.sums.total}</div></div>
    <div class="kpi"><div class="k">剔除 flaky 后</div><div class="v">${B.sums.stable_rate.toFixed(1)}%</div>
      <div class="d ${cls(ds)}">${(ds>=0?"↑ ":"↓ ")+Math.abs(ds)} pt　基准 ${A.sums.stable_rate.toFixed(1)}%</div></div>
    <div class="kpi"><div class="k">变化题数</div><div class="v">${better+worse}</div>
      <div class="d"><span class="up">↑ ${better}</span>　<span class="down">↓ ${worse}</span></div></div>`;
}

function renderCats(){
  const A = R[iA], B = R[iB];
  document.getElementById("cats").innerHTML = D.catOrder.filter(c => A.sums.cats[c] || B.sums.cats[c]).map(c => {
    const a = A.sums.cats[c], b = B.sums.cats[c];
    const va = a ? a.rate : 0, vb = b ? b.rate : 0, d = +(vb - va).toFixed(1);
    const cls = d > 0.5 ? "up" : d < -0.5 ? "down" : "flat";
    const col = d > 0.5 ? "#1D9E75" : d < -0.5 ? "#C0492A" : "#8CBFAC";
    return `<div class="bar-row">
      <div class="bar-name">${D.catName[c]||c}<span style="color:var(--t3)"> ·${(b||a).n}题</span></div>
      <div class="pair">
        <div class="tr"><div class="fl" style="width:${va}%;background:#CFCBC2"></div></div>
        <div class="tr"><div class="fl" style="width:${vb}%;background:${col}"></div></div>
      </div>
      <div class="bar-val">${va.toFixed(1)}% → ${vb.toFixed(1)}% <span class="${cls}">${d>0?"+":""}${d}</span></div>
    </div>`;
  }).join("");
}

function renderScatter(){
  const W = 900, H = 420, P = 52, iw = W-P*2, ih = H-P*2;
  const X = v => P + v*iw, Y = v => H - P - v*ih;
  let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">`;
  [0,.2,.4,.6,.8,1].forEach(t => {
    s += `<line x1="${X(t)}" y1="${Y(0)}" x2="${X(t)}" y2="${Y(1)}" stroke="#F0EEE8"/>`;
    s += `<line x1="${X(0)}" y1="${Y(t)}" x2="${X(1)}" y2="${Y(t)}" stroke="#F0EEE8"/>`;
    s += `<text x="${X(t)}" y="${Y(0)+20}" font-size="11" fill="#8C8A83" text-anchor="middle">${t}</text>`;
    s += `<text x="${P-12}" y="${Y(t)+4}" font-size="11" fill="#8C8A83" text-anchor="end">${t}</text>`;
  });
  s += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}" stroke="#C9C5BA" stroke-dasharray="5 5"/>`;
  s += `<text x="${X(0.96)}" y="${Y(0.06)}" font-size="11" fill="#B4B2A9" text-anchor="end">虚线＝无变化</text>`;
  s += `<text x="${W/2}" y="${H-8}" font-size="12" fill="#5F5E5A" text-anchor="middle">基准 w3</text>`;
  s += `<text x="14" y="${H/2}" font-size="12" fill="#5F5E5A" text-anchor="middle" transform="rotate(-90 14 ${H/2})">当前 w3</text>`;
  cur.forEach(it => {
    if (it.w3a == null || it.w3b == null) return;
    const d = it.w3b - it.w3a;
    const col = d > 0.02 ? "#1D9E75" : d < -0.02 ? "#C0492A" : "#A8A49A";
    s += `<circle cx="${X(it.w3a)}" cy="${Y(it.w3b)}" r="${Math.abs(d)>0.25?6:4}" fill="${col}" fill-opacity=".72" stroke="#fff" stroke-width=".8">` +
         `<title>#${it.id} ${esc(it.q)}\n基准 ${fmt(it.w3a)} → 当前 ${fmt(it.w3b)}（${sgn(d)}）</title></circle>`;
  });
  document.getElementById("scatter").innerHTML = s + `</svg>`;
  const off = cur.filter(i => i.w3a!=null && i.w3b!=null && Math.abs(i.w3b-i.w3a) > 0.05).length;
  document.getElementById("scatterNote").textContent =
    `${cur.length} 道题中只有 ${off} 道变化超过 0.05，其余贴着对角线——改动影响的是特定几道题，不是整体漂移。`;
}

function renderMovers(){
  const arr = cur.filter(i => i.w3a != null && i.w3b != null).map(i => ({...i, d: i.w3b - i.w3a}));
  const up = arr.filter(i => i.d > 0.02).sort((x,y) => y.d - x.d).slice(0,5);
  const dn = arr.filter(i => i.d < -0.02).sort((x,y) => x.d - y.d).slice(0,5);
  if (!up.length && !dn.length){
    document.getElementById("movers").innerHTML = '<p class="hint">两轮无超过 ±0.02 的变化。</p>'; return;
  }
  const max = Math.max(...[...up,...dn].map(i => Math.abs(i.d)), 0.01);
  const row = (i, sign) => `<div class="bar-row" style="grid-template-columns:1fr 240px 64px">
      <div class="bar-name" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">#${i.id} ${esc(i.q)}</div>
      <div class="pair"><div class="tr"><div class="fl" style="width:${Math.abs(i.d)/max*100}%;background:${sign>0?"#1D9E75":"#C0492A"}"></div></div></div>
      <div class="bar-val ${sign>0?"up":"down"}">${sgn(i.d)}</div></div>`;
  const cap = t => `<div style="font-size:12px;color:var(--t3);margin:2px 0 9px">${t}</div>`;
  document.getElementById("movers").innerHTML =
    (up.length ? cap("↑ 提升") + up.map(i=>row(i,1)).join("") : "") +
    (dn.length ? cap("↓ 退步") + dn.map(i=>row(i,-1)).join("") : "");
}

function renderFilters(){
  const counts = {};
  cur.forEach(i => counts[i.cat] = (counts[i.cat]||0) + 1);
  const chips = [`<button class="chip ${filter==="all"?"on":""}" data-c="all">全部 ${cur.length}</button>`]
    .concat(D.catOrder.filter(c => counts[c]).map(c =>
      `<button class="chip ${filter===c?"on":""}" data-c="${c}">${D.catName[c]||c} ${counts[c]}</button>`));
  const el = document.getElementById("filters");
  el.innerHTML = chips.join("");
  el.querySelectorAll(".chip").forEach(b => b.onclick = () => { filter = b.dataset.c; renderFilters(); renderTable(); });
}

function renderTable(){
  const rows = cur.filter(i => filter === "all" || i.cat === filter);
  document.getElementById("tbody").innerHTML = rows.map(i => {
    const d = (i.w3a==null || i.w3b==null) ? null : i.w3b - i.w3a;
    const cls = d == null ? "flat" : d > 0.02 ? "up" : d < -0.02 ? "down" : "flat";
    const res = i.hitb == null ? "" : (i.hitb ? '<span class="tag hit">通过</span>' : '<span class="tag miss">未过</span>');
    return `<tr><td class="num">${i.id}</td>
      <td><span class="tag">${D.catName[i.cat]||i.cat}</span></td>
      <td>${esc(i.q)}</td>
      <td class="num">${fmt(i.w3a)}</td><td class="num">${fmt(i.w3b)}</td>
      <td class="num ${cls}">${d==null?"—":sgn(d)}</td><td>${res}</td></tr>`;
  }).join("");
}

function renderAll(noHash){
  cur = pair();
  const A = R[iA], B = R[iB];
  document.getElementById("note").textContent =
    iA === iB ? "同一轮，无对比" : `${Math.abs(iB-iA)} 轮之差`;
  renderKpis(); renderCats(); renderScatter(); renderMovers(); renderFilters(); renderTable();
  if (!noHash) location.hash = iA + "-" + iB;
}

function initSelects(){
  const opt = r => `<option value="${r.i}">${esc(label(r))}</option>`;
  const sa = document.getElementById("selA"), sb = document.getElementById("selB");
  sa.innerHTML = R.map((r,i) => ({...r, i})).map(opt).join("");
  sb.innerHTML = sa.innerHTML;
  sa.value = iA; sb.value = iB;
  sa.onchange = () => { iA = +sa.value; renderAll(); };
  sb.onchange = () => { iB = +sb.value; renderAll(); };
  document.getElementById("swapBtn").onclick = () => {
    [iA, iB] = [iB, iA];
    sa.value = iA; sb.value = iB; renderAll();
  };
}

window.addEventListener("hashchange", () => {
  const m = location.hash.match(/^#(\d+)-(\d+)$/);
  if (!m) return;
  const a = +m[1], b = +m[2];
  if (a >= R.length || b >= R.length || (a === iA && b === iB)) return;
  iA = a; iB = b;
  document.getElementById("selA").value = iA;
  document.getElementById("selB").value = iB;
  renderAll(true);
});

function boot(){
  R.forEach(r => { if (!r.label) r.label = r.name; });
  const m = location.hash.match(/^#(\d+)-(\d+)$/);
  if (m && +m[1] < R.length && +m[2] < R.length){ iA = +m[1]; iB = +m[2]; }
  document.getElementById("sub").textContent =
    `${R.length} 轮历史数据　·　生成于 ${D.built}`;
  document.getElementById("foot").textContent =
    "由 eval/report.py 生成　·　数据源 eval/results/*.csv";
  initSelects(); renderAll();
}
boot();
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="生成评估前后对比报告")
    ap.add_argument("files", nargs="*", help="可选：指定若干 CSV；留空则载入 results/ 全部")
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "report.html"))
    ap.add_argument("--open", action="store_true", help="生成后自动打开")
    args = ap.parse_args()

    paths = args.files or sorted(glob.glob(os.path.join(RESULTS, "*.csv")))
    if not paths:
        sys.exit("没有找到 CSV，先跑 run_eval.py --run")

    rounds = []
    for p in paths:
        rnd = load_round(p)
        if not rnd:
            print("跳过（读不到数据）：%s" % p)
            continue
        rnd["sums"] = summarize(rnd)
        rnd["label"] = friendly(rnd["name"])
        rounds.append(rnd)

    if not rounds:
        sys.exit("没有可用的轮次")

    html = build_html(rounds)
    io.open(args.out, "w", encoding="utf-8", newline="\n").write(html)
    print("已生成 %s（%.0f KB，%d 轮）" % (args.out, os.path.getsize(args.out)/1024.0, len(rounds)))
    for r in rounds:
        print("  %-34s %2d题  命中 %5.1f%%" % (r["label"], r["sums"]["total"], r["sums"]["rate"]))
    if len(rounds) >= 2:
        a, b = rounds[-2]["sums"], rounds[-1]["sums"]
        print("\n默认对比：%s → %s　%+.1f pt" % (rounds[-2]["label"], rounds[-1]["label"], b["rate"]-a["rate"]))
    if args.open:
        webbrowser.open("file:///" + os.path.abspath(args.out).replace("\\", "/"))


if __name__ == "__main__":
    main()
