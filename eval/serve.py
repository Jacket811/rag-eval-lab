#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RAG 评估 · 本地控制台

一条命令（或双击 start.bat）启动，浏览器里点按钮就能跑评估、看报告。
不需要 Flask/FastAPI，只用 Python 标准库；不需要记命令、不需要记文件名。

用法：
    python serve.py                # 启动并自动打开浏览器
    python serve.py --port 9000    # 指定端口
    python serve.py --no-open      # 不自动开浏览器

原理：
    后端只做三件事——环境自检、以子进程方式跑 run_eval.py、把 stdout 转给前端。
    评估逻辑一行都没改，还是那个 run_eval.py。
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_PORT = 8899

# ===== 运行时状态 =====
STATE = {
    "running": False,
    "lines": [],        # 评估过程中的输出行
    "csv": None,        # 跑完后生成的 CSV
    "meta": None,
    "started_at": None,
    "tag": "",
    "returncode": None,
    "error": None,
}
LOCK = threading.Lock()
PROC = {"p": None}


# ===================== 环境探测 =====================

def read_questions():
    """题库概要"""
    p = os.path.join(HERE, "questions.json")
    if not os.path.exists(p):
        return {"ok": False, "error": "找不到 questions.json"}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (ValueError, IOError) as e:
        return {"ok": False, "error": str(e)[:80]}
    cats = {}
    for it in d.get("items", []):
        cats[it.get("cat", "?")] = cats.get(it.get("cat", "?"), 0) + 1
    return {
        "ok": True,
        "total": len(d.get("items", [])),
        "cats": cats,
        "collection": d.get("meta", {}).get("collection", ""),
        "desc": d.get("meta", {}).get("desc", ""),
    }


def read_containers():
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=15, errors="replace")
        names = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
        need = ["open-webui", "pgvector", "ollama"]
        return {
            "all": names,
            "missing": [n for n in need if n not in names],
        }
    except (OSError, subprocess.SubprocessError) as e:
        return {"all": [], "missing": ["docker 不可用"], "error": str(e)[:60]}


def read_past_runs():
    """历史轮次：读 results/ 下的 CSV 概览"""
    d = os.path.join(HERE, "results")
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".csv"):
            continue
        p = os.path.join(d, f)
        total = hits = 0
        try:
            import csv as _csv
            with open(p, encoding="utf-8-sig") as fh:
                for row in _csv.DictReader(fh):
                    total += 1
                    try:
                        hits += int(float(row.get("hit") or 0))
                    except ValueError:
                        pass
        except (IOError, ValueError):
            continue
        meta_path = os.path.splitext(p)[0] + ".meta.json"
        out.append({
            "name": f[:-4],
            "total": total,
            "hits": hits,
            "rate": round(100.0 * hits / total, 1) if total else 0,
            "has_meta": os.path.exists(meta_path),
            "mtime": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p))),
        })
    return out[-20:]


def read_token_status():
    """能否拿到 Open WebUI token（决定了能不能跑）"""
    try:
        import run_eval
        tok = run_eval.get_token()
        return {"ok": bool(tok), "hint": "" if tok else "拿不到 token"}
    except SystemExit:
        return {"ok": False, "hint": "拿不到 token：检查 open-webui 容器是否在运行"}
    except Exception as e:
        return {"ok": False, "hint": str(e)[:100]}


# ===================== 评估执行 =====================

def run_job(tag):
    """子进程跑 run_eval.py，逐行收 stdout"""
    cmd = [sys.executable, "-u", os.path.join(HERE, "run_eval.py"), "--run"]
    if tag:
        cmd += ["--tag", tag]

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    with LOCK:
        STATE.update({"running": True, "lines": [], "csv": None, "meta": None,
                      "error": None, "returncode": None,
                      "started_at": time.time(), "tag": tag})

    try:
        proc = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
        PROC["p"] = proc
        for line in proc.stdout:
            line = line.rstrip("\n")
            with LOCK:
                STATE["lines"].append(line)
                if len(STATE["lines"]) > 4000:
                    STATE["lines"] = STATE["lines"][-3000:]
                m = re.search(r"结果已写入\s+(.+\.csv)", line)
                if m:
                    STATE["csv"] = os.path.basename(m.group(1).strip())
                m = re.search(r"参数快照\s+(.+\.meta\.json)", line)
                if m:
                    STATE["meta"] = os.path.basename(m.group(1).strip())
        proc.wait()
        with LOCK:
            STATE["returncode"] = proc.returncode
    except Exception as e:
        with LOCK:
            STATE["error"] = str(e)[:200]
    finally:
        with LOCK:
            STATE["running"] = False


def regenerate_report():
    """跑完顺手重生成报告，让新轮次出现在下拉框里"""
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "report.py")],
                           cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ===================== HTTP =====================

class Handler(BaseHTTPRequestHandler):
    server_version = "RAGEvalConsole/1.0"

    def log_message(self, fmt, *args):
        pass  # 静音，避免刷屏

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, name, ctype):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            self._send(404, {"error": "not found: " + name})
            return
        with open(p, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path in ("/", "/index.html"):
            return self._file("console.html", "text/html; charset=utf-8")
        if path == "/report.html":
            return self._file("report.html", "text/html; charset=utf-8")

        if path == "/api/state":
            with LOCK:
                snap = {
                    "running": STATE["running"],
                    "csv": STATE["csv"],
                    "meta": STATE["meta"],
                    "error": STATE["error"],
                    "returncode": STATE["returncode"],
                    "tag": STATE["tag"],
                    "elapsed": int(time.time() - STATE["started_at"]) if STATE["started_at"] else 0,
                }
                n_lines = len(STATE["lines"])
            return self._send(200, {
                "questions": read_questions(),
                "containers": read_containers(),
                "past": read_past_runs(),
                "job": snap,
                "logCount": n_lines,
            })

        if path == "/api/log":
            since = int((q.get("since") or ["0"])[0])
            with LOCK:
                lines = STATE["lines"][since:]
                total = len(STATE["lines"])
                running = STATE["running"]
                done_csv = STATE["csv"]
            return self._send(200, {"lines": lines, "next": total,
                                    "running": running, "csv": done_csv})

        if path == "/api/check":
            return self._send(200, {"token": read_token_status()})

        self._send(404, {"error": "unknown path"})

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            body = {}

        if u.path == "/api/run":
            with LOCK:
                if STATE["running"]:
                    return self._send(409, {"error": "已经在跑了"})
            q = read_questions()
            if not q.get("ok"):
                return self._send(400, {"error": "题库读不到：" + q.get("error", "")})
            tag = (body.get("tag") or "").strip()
            tag = re.sub(r"[^\w\u4e00-\u9fa5.-]+", "_", tag)[:40]
            threading.Thread(target=run_job, args=(tag,), daemon=True).start()
            return self._send(200, {"ok": True, "tag": tag})

        if u.path == "/api/stop":
            proc = PROC.get("p")
            stopped = False
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    stopped = True
                except OSError:
                    pass
            return self._send(200, {"ok": True, "stopped": stopped})

        if u.path == "/api/report":
            ok = regenerate_report()
            return self._send(200, {"ok": ok})

        self._send(404, {"error": "unknown path"})


def pick_port(start):
    for p in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def main():
    ap = argparse.ArgumentParser(description="RAG 评估本地控制台")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(os.path.join(HERE, "console.html")):
        sys.exit("缺少 console.html，请确认文件完整")

    port = pick_port(args.port)
    url = "http://127.0.0.1:%d/" % port

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("=" * 56)
    print("  RAG 评估 · 本地控制台")
    print("  地址: %s" % url)
    print("  按 Ctrl+C 停止")
    print("=" * 56)

    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
