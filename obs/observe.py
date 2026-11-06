#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RAG 系统可观测快照

用法：
    python observe.py            # 采集一次，追加到 snapshots.csv
    python observe.py --report   # 打印趋势表（不采集）
    python observe.py --watch    # 采集后立即打印报告

设计原则：
  1. 只读，不改任何线上数据
  2. 只采「能从系统里读到的客观数字」，不估算
  3. 每次一行累积，靠历史快照形成趋势
"""
import argparse
import csv
import datetime
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "snapshots.csv")

# ===== 环境配置 =====
# 默认值适用于 docker compose 默认服务名；换环境时用环境变量覆盖，不必改代码
OWUI_CONTAINER = os.environ.get("OWUI_CONTAINER", "open-webui")
OLLAMA_CONTAINER = os.environ.get("OLLAMA_CONTAINER", "ollama")
PG_CONTAINER = os.environ.get("PG_CONTAINER", "pgvector")
PG_USER = os.environ.get("PG_USER", "owui")
PG_DB = os.environ.get("PG_DB", "openwebui")
ALL_CONTAINERS = os.environ.get(
    "ALL_CONTAINERS",
    "open-webui,ollama,pgvector,reranker,rerank-adapter",
).split(",")

FIELDS = [
    "时间", "回答数", "有资料", "无资料占比", "空回答",
    "命中中位", "命中p10", "低命占比",
    "prompt中位", "prompt_p90", "生成速度", "重启总数", "库体积MB",
]

# 在容器内跑的采集脚本：直接从 SQLite 读，不依赖任何接口
COLLECT = r'''
import sqlite3, json, statistics
c = sqlite3.connect('/app/backend/data/webui.db')
msgs = []
for cid, payload in c.execute('select id, chat from chat'):
    try:
        d = json.loads(payload)
    except Exception:
        continue
    hist = (d.get('history') or {}).get('messages', {})
    for k, v in hist.items():
        if not v or v.get('role') != 'assistant':
            continue
        u = v.get('usage') or {}
        ds = []
        for s in (v.get('sources') or []):
            ds += (s.get('distances') or [])
        msgs.append({
            'prompt': u.get('prompt_eval_count') or 0,
            'eval': u.get('eval_count') or 0,
            'scores': ds,
            'body': (v.get('content') or '').strip(),
        })
hits = []
for m in msgs:
    if not m['scores']:
        continue
    s = sorted(m['scores'], reverse=True)
    hits.append(0.5*s[0] + 0.3*(s[1] if len(s) > 1 else 0) + 0.2*(s[2] if len(s) > 2 else 0))
p = [m['prompt'] for m in msgs if m['prompt']]
print(json.dumps({
    'total': len(msgs),
    'with_src': len(hits),
    'empty': sum(1 for m in msgs if not m['body']),
    'hits': hits,
    'prompt': p,
}))
'''


def run(cmd, merge_err=False):
    r = subprocess.run(cmd, text=True, encoding="utf-8", errors="ignore",
                       stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT if merge_err else subprocess.PIPE)
    return (r.stdout or "").strip()


def collect_db():
    out = run(["docker", "exec", OWUI_CONTAINER, "python", "-c", COLLECT])
    # 容器启动日志可能混进 stdout，取最后一个 JSON 行
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:
                continue
    raise RuntimeError("采集失败，容器输出：%s" % out[-300:])


def collect_restarts():
    names = ALL_CONTAINERS
    total = 0
    for n in names:
        v = run(["docker", "inspect", n, "--format", "{{.RestartCount}}"])
        if v.isdigit():
            total += int(v)
    return total


def collect_tps():
    """从 ollama 日志里取最近几次生成速度（token/s）的中位数
    注意：docker logs 输出在 stderr，必须合并捕获"""
    out = run(["docker", "logs", "--tail", "500", OLLAMA_CONTAINER], merge_err=True)
    hits = [float(x) for x in re.findall(r"eval time\s*=\s*[\d.]+ ms\s*/\s*\d+ tokens.*?([\d.]+) tokens per second", out)]
    if not hits:
        return None
    hits = hits[-5:]
    hits.sort()
    return round(hits[len(hits) // 2], 1)


def collect_db_size():
    out = run(["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB,
               "-t", "-A", "-c", "select pg_database_size('openwebui')"])
    return round(int(out) / 1024 / 1024, 1) if out.isdigit() else None


def pct(x, n):
    return round(100.0 * x / n, 1) if n else 0.0


def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def snapshot():
    db = collect_db()
    hits = sorted(db["hits"])
    prompt = sorted(db["prompt"])
    total = db["total"]
    low = sum(1 for h in hits if h < 0.4)

    row = {
        "时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "回答数": total,
        "有资料": db["with_src"],
        "无资料占比": pct(total - db["with_src"], total),
        "空回答": db["empty"],
        "命中中位": round(quantile(hits, 0.5), 3) if hits else "",
        "命中p10": round(quantile(hits, 0.1), 3) if hits else "",
        "低命占比": pct(low, db["with_src"]),
        "prompt中位": int(quantile(prompt, 0.5)) if prompt else "",
        "prompt_p90": int(quantile(prompt, 0.9)) if prompt else "",
        "生成速度": collect_tps() or "",
        "重启总数": collect_restarts(),
        "库体积MB": collect_db_size() or "",
    }

    new_file = not os.path.exists(CSV_PATH)
    with io.open(CSV_PATH, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    return row


def load_rows():
    if not os.path.exists(CSV_PATH):
        return []
    with io.open(CSV_PATH, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def report():
    rows = load_rows()
    if not rows:
        print("还没有快照，先运行 python observe.py")
        return
    cols = ["时间", "回答数", "有资料", "无资料占比", "空回答",
            "命中中位", "命中p10", "低命占比", "prompt中位", "prompt_p90"]
    widths = [16, 7, 7, 10, 7, 9, 8, 8, 10, 10]
    line = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    print(line)
    print("-" * len(line.encode("gbk", errors="ignore")))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))
    print()
    if len(rows) >= 2:
        a, b = rows[0], rows[-1]
        def delta(key):
            try:
                return float(b[key]) - float(a[key])
            except Exception:
                return None
        d = delta("命中中位")
        if d is not None:
            arrow = "↑" if d > 0.005 else ("↓" if d < -0.005 else "→")
            print("命中中位 %s → %s  %s %+.3f" % (a["命中中位"], b["命中中位"], arrow, d))
        for k in ("无资料占比", "低命占比", "prompt中位"):
            dv = delta(k)
            if dv is not None:
                print("%-8s %s → %s  %+.1f" % (k, a[k], b[k], dv))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只打印趋势，不采集")
    ap.add_argument("--watch", action="store_true", help="采集后打印趋势")
    a = ap.parse_args()

    if a.report:
        report()
        sys.exit(0)

    r = snapshot()
    print("已采集：", json.dumps(r, ensure_ascii=False))
    print("累积到", CSV_PATH)
    if a.watch:
        print()
        report()
