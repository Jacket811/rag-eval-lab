#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把两轮评估 CSV 渲染成单文件 HTML 报告

用法：
    python report.py                                   # 自动取 results/ 里最新两个
    python report.py a.csv b.csv -o report.html        # 指定前后两轮
    python report.py a.csv                             # 只渲染一轮

设计：零外部依赖，图表用内联 SVG，数据和脚本全部内嵌，
单个 html 双击可开、可离线、可直接放 GitHub Pages。
"""
import argparse
import collections
import csv
import datetime
import glob
import io
import json
import os
import sys

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


def load(path):
    rows = list(csv.DictReader(io.open(path, encoding="utf-8-sig")))
    out = {}
    for r in rows:
        try:
            out[str(r["id"])] = {
                "id": int(r["id"]),
                "cat": r["cat"],
                "q": r["q"],
                "top1": float(r["top1"] or 0),
                "w3": float(r["w3"] or 0),
                "hit": int(float(r["hit"] or 0)),
                "seg": int(float(r.get("segments") or 0)),
                "diff": r.get("difficulty", ""),
                "flaky": int(float(r.get("flaky") or 0)),
            }
        except (ValueError, KeyError):
            continue
    return out


def pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def summarize(data):
    total = len(data)
    hit = sum(v["hit"] for v in data.values())
    stable = [v for v in data.values() if not v["flaky"]]
    hit_s = sum(v["hit"] for v in stable)
    by_cat = collections.OrderedDict()
    for c in CAT_ORDER:
        items = [v for v in data.values() if v["cat"] == c]
        if not items:
            continue
        by_cat[c] = {
            "n": len(items),
            "hit": sum(v["hit"] for v in items),
            "rate": pct(sum(v["hit"] for v in items), len(items)),
        }
    return {
        "total": total,
        "hit": hit,
        "rate": pct(hit, total),
        "stable_total": len(stable),
        "stable_rate": pct(hit_s, len(stable)),
        "cats": by_cat,
    }


def merge(A, B):
    items = []
    for qid in sorted(A, key=lambda x: int(x)):
        a = A[qid]
        b = B.get(qid)
        items.append({
            "id": a["id"],
            "cat": a["cat"],
            "q": a["q"],
            "w3a": a["w3"],
            "w3b": b["w3"] if b else None,
            "hita": a["hit"],
            "hitb": b["hit"] if b else None,
            "top1a": a["top1"],
            "top1b": b["top1"] if b else None,
            "seg": a["seg"],
            "diff": a["diff"],
            "flaky": a["flaky"],
        })
    return items


def build_html(items, sumA, sumB, meta):
    payload = json.dumps({
        "items": items,
        "a": sumA,
        "b": sumB,
        "catName": CAT_NAME,
        "catOrder": CAT_ORDER,
        "meta": meta,
    }, ensure_ascii=False)

    return HTML_TEMPLATE.replace("__DATA__", payload)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAG 检索评估报告</title>
<style>
:root{
  --bg:#FBFAF7; --card:#FFFFFF; --line:#E5E2DA;
  --t1:#1F1E1B; --t2:#5F5E5A; --t3:#8C8A83;
  --good:#1D9E75; --bad:#C0492A; --gray:#888780;
  --good-bg:#E1F5EE; --bad-bg:#FAECE7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--t1);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  padding:32px 20px 80px}
.wrap{max-width:980px;margin:0 auto}
h1{font-size:22px;font-weight:600;margin:0 0 4px}
.sub{color:var(--t3);font-size:13px;margin-bottom:28px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:18px}
h2{font-size:15px;font-weight:600;margin:0 0 4px}
.hint{color:var(--t3);font-size:12px;margin:0 0 18px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.kpi .k{font-size:12px;color:var(--t3);margin-bottom:6px}
.kpi .v{font-size:26px;font-weight:600;letter-spacing:-.5px}
.kpi .d{font-size:12px;margin-top:4px}
.up{color:var(--good)} .down{color:var(--bad)} .flat{color:var(--t3)}
.bar-row{display:grid;grid-template-columns:88px 1fr 132px;align-items:center;gap:12px;margin-bottom:10px}
.bar-name{font-size:13px;color:var(--t2)}
.bar-track{position:relative;height:22px;background:#F3F1EC;border-radius:4px;overflow:hidden}
.pair{display:flex;flex-direction:column;gap:3px}
.pair .tr{position:relative;height:8px;background:#F3F1EC;border-radius:3px;overflow:hidden}
.pair .fl{position:absolute;left:0;top:0;height:100%;border-radius:3px}
.bar-fill{position:absolute;left:0;top:0;height:100%;border-radius:4px}
.bar-val{font-size:12px;text-align:right;color:var(--t2);font-variant-numeric:tabular-nums}
.legend{display:flex;gap:16px;font-size:12px;color:var(--t3);margin-bottom:14px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.chip{border:1px solid var(--line);background:#fff;border-radius:20px;padding:5px 13px;font-size:12px;
  cursor:pointer;color:var(--t2)}
.chip:hover{border-color:#C9C5BA}
.chip.on{background:var(--t1);border-color:var(--t1);color:#fff}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:500;color:var(--t3);font-size:12px;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:8px 10px;border-bottom:1px solid #F2F0EB;vertical-align:top}
tr:hover td{background:#FCFBF8}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;font-size:11px;padding:2px 7px;border-radius:10px;background:#F3F1EC;color:var(--t2);white-space:nowrap}
.tag.hit{background:var(--good-bg);color:#0F6E56}
.tag.miss{background:var(--bad-bg);color:#993C1D}
svg text{font-family:inherit}
.scatter-note{font-size:12px;color:var(--t3);margin-top:10px}
footer{text-align:center;color:var(--t3);font-size:12px;margin-top:30px}
@media(max-width:760px){.kpis{grid-template-columns:repeat(2,1fr)}.bar-row{grid-template-columns:66px 1fr 78px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>RAG 检索层评估报告</h1>
  <div class="sub" id="meta"></div>

  <div class="kpis" id="kpis"></div>

  <div class="card">
    <h2>分类命中率</h2>
    <p class="hint">每类通过率。上行灰色＝改动前，下行彩色＝改动后。样本少于 5 题的类别结论不可靠。</p>
    <div class="legend">
      <span><i style="background:#CFCBC2"></i>上行：改动前</span>
      <span><i style="background:#1D9E75"></i>下行：改动后（变差为红）</span>
    </div>
    <div id="cats"></div>
  </div>

  <div class="card">
    <h2>逐题变化散点</h2>
    <p class="hint">横轴改前分数，纵轴改后分数。落在对角线上＝没变；越往左上＝提升越大，右下＝退步。</p>
    <div id="scatter"></div>
    <div class="scatter-note" id="scatterNote"></div>
  </div>

  <div class="card">
    <h2>提升与退步最大的题</h2>
    <p class="hint">按前三段加权分（w3）的变化排序，阈值 ±0.02（与 run_eval.py --diff 一致）。</p>
    <div id="movers"></div>
  </div>

  <div class="card">
    <h2>全部题目</h2>
    <div class="tools" id="filters"></div>
    <table>
      <thead>
        <tr>
          <th style="width:44px">#</th><th style="width:76px">类别</th><th>问题</th>
          <th class="num" style="width:66px">改前</th><th class="num" style="width:66px">改后</th>
          <th class="num" style="width:64px">变化</th><th style="width:70px">结果</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

  <footer id="foot"></footer>
</div>

<script>
const D = __DATA__;
const A = D.a, B = D.b, items = D.items;
const hasB = B && B.total > 0;

const fmt = (x, n=3) => x == null ? "—" : Number(x).toFixed(n);
const sgn = x => (x > 0 ? "+" : "") + x.toFixed(3);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

document.getElementById("meta").textContent = D.meta.line;

function renderKpis(){
  const el = document.getElementById("kpis");
  const d = hasB ? +(B.rate - A.rate).toFixed(1) : null;
  const ds = hasB ? +(B.stable_rate - A.stable_rate).toFixed(1) : null;
  const better = hasB ? items.filter(i => i.w3b != null && i.w3b - i.w3a > 0.02).length : 0;
  const worse  = hasB ? items.filter(i => i.w3b != null && i.w3a - i.w3b > 0.02).length : 0;
  const cls = v => v == null ? "flat" : (v > 0 ? "up" : v < 0 ? "down" : "flat");
  el.innerHTML = `
    <div class="kpi"><div class="k">总命中率</div>
      <div class="v">${B.rate.toFixed(1)}%</div>
      <div class="d ${cls(d)}">${hasB ? (d>=0?"↑ ":"↓ ") + Math.abs(d) + " pt（改前 " + A.rate.toFixed(1) + "%）" : "单轮结果"}</div></div>
    <div class="kpi"><div class="k">通过题数</div>
      <div class="v">${B.hit}/${B.total}</div>
      <div class="d flat">改前 ${A.hit}/${A.total}</div></div>
    <div class="kpi"><div class="k">剔除 flaky 后</div>
      <div class="v">${B.stable_rate.toFixed(1)}%</div>
      <div class="d ${cls(ds)}">${hasB ? (ds>=0?"↑ ":"↓ ") + Math.abs(ds) + " pt（改前 " + A.stable_rate.toFixed(1) + "%）" : "—"}</div></div>
    <div class="kpi"><div class="k">变化题数</div>
      <div class="v">${better + worse}</div>
      <div class="d"><span class="up">↑ ${better}</span>　<span class="down">↓ ${worse}</span></div></div>`;
}

function renderCats(){
  const el = document.getElementById("cats");
  const rows = D.catOrder.filter(c => B.cats[c] || A.cats[c]).map(c => {
    const a = A.cats[c], b = B.cats[c];
    const va = a ? a.rate : 0, vb = b ? b.rate : 0;
    const d = hasB ? (vb - va) : null;
    const cls = d == null ? "flat" : d > 0.5 ? "up" : d < -0.5 ? "down" : "flat";
    const colB = !hasB ? "#1D9E75" : (d > 0.5 ? "#1D9E75" : d < -0.5 ? "#C0492A" : "#8CBFAC");
    return `<div class="bar-row" style="align-items:center">
      <div class="bar-name">${D.catName[c] || c}<span style="color:var(--t3)"> ·${(b||a).n}题</span></div>
      <div class="pair">
        <div class="tr"><div class="fl" style="width:${va}%;background:#CFCBC2"></div></div>
        <div class="tr"><div class="fl" style="width:${vb}%;background:${colB}"></div></div>
      </div>
      <div class="bar-val">${hasB ? va.toFixed(1) + "% → " : ""}${vb.toFixed(1)}% <span class="${cls}">${hasB ? (d>0?"+":"") + d.toFixed(1) : ""}</span></div>
    </div>`;
  }).join("");
  el.innerHTML = rows;
}

function renderScatter(){
  const W = 900, H = 420, P = 52;
  const iw = W - P*2, ih = H - P*2;
  const X = v => P + v * iw, Y = v => H - P - v * ih;
  const ticks = [0,0.2,0.4,0.6,0.8,1];
  let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">`;
  ticks.forEach(t => {
    s += `<line x1="${X(t)}" y1="${Y(0)}" x2="${X(t)}" y2="${Y(1)}" stroke="#F0EEE8" stroke-width="1"/>`;
    s += `<line x1="${X(0)}" y1="${Y(t)}" x2="${X(1)}" y2="${Y(t)}" stroke="#F0EEE8" stroke-width="1"/>`;
    s += `<text x="${X(t)}" y="${Y(0)+20}" font-size="11" fill="#8C8A83" text-anchor="middle">${t}</text>`;
    s += `<text x="${P-12}" y="${Y(t)+4}" font-size="11" fill="#8C8A83" text-anchor="end">${t}</text>`;
  });
  s += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}" stroke="#C9C5BA" stroke-width="1" stroke-dasharray="5 5"/>`;
  s += `<text x="${X(0.88)}" y="${Y(0.10)}" font-size="11" fill="#B4B2A9" text-anchor="end">虚线＝无变化</text>`;
  s += `<text x="${W/2}" y="${H-10}" font-size="12" fill="#5F5E5A" text-anchor="middle">改动前 w3</text>`;
  s += `<text x="14" y="${H/2}" font-size="12" fill="#5F5E5A" text-anchor="middle" transform="rotate(-90 14 ${H/2})">改动后 w3</text>`;

  items.forEach(it => {
    if (it.w3b == null) return;
    const d = it.w3b - it.w3a;
    const col = d > 0.02 ? "#1D9E75" : d < -0.02 ? "#C0492A" : "#A8A49A";
    const r = Math.abs(d) > 0.25 ? 6 : 4;
    s += `<circle cx="${X(it.w3a)}" cy="${Y(it.w3b)}" r="${r}" fill="${col}" fill-opacity=".72" stroke="#fff" stroke-width=".8">`;
    s += `<title>#${it.id} ${esc(it.q)}\n改前 ${fmt(it.w3a)} → 改后 ${fmt(it.w3b)}（${sgn(d)}）</title></circle>`;
  });
  s += `</svg>`;
  document.getElementById("scatter").innerHTML = s;

  const off = items.filter(i => i.w3b != null && Math.abs(i.w3b - i.w3a) > 0.05).length;
  document.getElementById("scatterNote").textContent =
    `${items.length} 道题中，${off} 道变化超过 0.05，其余基本贴着对角线——说明这次改动只影响了特定几道题。`;
}

function renderMovers(){
  if (!hasB) { document.getElementById("movers").innerHTML = '<p class="hint">单轮数据，无对比。</p>'; return; }
  const arr = items.filter(i => i.w3b != null).map(i => ({...i, d: i.w3b - i.w3a}));
  const up = arr.filter(i => i.d > 0).sort((x,y) => y.d - x.d).slice(0, 5);
  const dn = arr.filter(i => i.d < 0).sort((x,y) => x.d - y.d).slice(0, 5);
  const max = Math.max(...[...up, ...dn].map(i => Math.abs(i.d)), 0.01);
  const row = (i, sign) => `<div class="bar-row" style="grid-template-columns:1fr 260px 62px">
      <div class="bar-name" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">#${i.id} ${esc(i.q)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.abs(i.d)/max*100}%;background:${sign>0?"#1D9E75":"#C0492A"}"></div></div>
      <div class="bar-val ${sign>0?"up":"down"}">${sgn(i.d)}</div>
    </div>`;
  document.getElementById("movers").innerHTML =
    up.map(i => row(i, 1)).join("") + dn.map(i => row(i, -1)).join("");
}

let filter = "all";
function renderFilters(){
  const counts = {};
  items.forEach(i => counts[i.cat] = (counts[i.cat] || 0) + 1);
  const chips = [`<button class="chip on" data-c="all">全部 ${items.length}</button>`]
    .concat(D.catOrder.filter(c => counts[c]).map(c =>
      `<button class="chip" data-c="${c}">${D.catName[c] || c} ${counts[c]}</button>`));
  const el = document.getElementById("filters");
  el.innerHTML = chips.join("");
  el.querySelectorAll(".chip").forEach(b => b.onclick = () => {
    el.querySelectorAll(".chip").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    filter = b.dataset.c;
    renderTable();
  });
}

function renderTable(){
  const rows = items.filter(i => filter === "all" || i.cat === filter);
  document.getElementById("tbody").innerHTML = rows.map(i => {
    const d = i.w3b == null ? null : i.w3b - i.w3a;
    const cls = d == null ? "flat" : d > 0.02 ? "up" : d < -0.02 ? "down" : "flat";
    const res = i.hitb == null ? "" : (i.hitb ? '<span class="tag hit">通过</span>' : '<span class="tag miss">未过</span>');
    return `<tr>
      <td class="num">${i.id}</td>
      <td><span class="tag">${D.catName[i.cat] || i.cat}</span></td>
      <td>${esc(i.q)}</td>
      <td class="num">${fmt(i.w3a)}</td>
      <td class="num">${fmt(i.w3b)}</td>
      <td class="num ${cls}">${d == null ? "—" : sgn(d)}</td>
      <td>${res}</td>
    </tr>`;
  }).join("");
}

document.getElementById("foot").textContent = D.meta.foot;
renderKpis(); renderCats(); renderScatter(); renderMovers(); renderFilters(); renderTable();
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="生成单文件 HTML 评估报告")
    ap.add_argument("files", nargs="*", help="A.csv [B.csv]，留空则自动取 results/ 最新两个")
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "report.html"))
    args = ap.parse_args()

    files = list(args.files)
    if not files:
        found = sorted(glob.glob(os.path.join(RESULTS, "*.csv")))
        if not found:
            sys.exit("results/ 下没有 CSV，先跑 run_eval.py")
        files = found[-2:] if len(found) >= 2 else found

    A = load(files[0])
    B = load(files[1]) if len(files) > 1 else {}
    if not A:
        sys.exit("读不到数据：%s" % files[0])

    sumA = summarize(A)
    sumB = summarize(B) if B else {"total": 0, "hit": 0, "rate": 0,
                                   "stable_total": 0, "stable_rate": 0, "cats": {}}
    items = merge(A, B)

    nameA = os.path.basename(files[0]).replace(".csv", "")
    nameB = os.path.basename(files[1]).replace(".csv", "") if len(files) > 1 else ""
    line = "%s　→　%s" % (nameA, nameB) if nameB else nameA
    foot = "由 report.py 生成于 %s　·　数据源 eval/results/" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    html = build_html(items, sumA, sumB, {"line": line, "foot": foot})
    io.open(args.out, "w", encoding="utf-8", newline="\n").write(html)
    print("已生成 %s（%.0f KB）" % (args.out, os.path.getsize(args.out) / 1024.0))
    if B:
        print("命中率 %.1f%% → %.1f%%（%+.1f pt）" % (sumA["rate"], sumB["rate"], sumB["rate"] - sumA["rate"]))


if __name__ == "__main__":
    main()
