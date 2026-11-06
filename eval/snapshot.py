#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
评估参数快照 —— 每轮评估跑完，把「当时的配置」存成 results/<同名>.meta.json

为什么需要：
    CSV 只有分数，没有配置。一个月后看 xxx-after-alias.csv，你不知道当时的
    top_k 是多少、阈值多少、跑的哪版代码，也就没法复现结论。

用法：
    python snapshot.py                  # 只打印当前配置，不写文件
    python snapshot.py -o cur.json      # 存成文件
    （run_eval.py --run 会自动调用 save()）

记录范围：只记影响检索效果的配置项，不记 API key 等敏感值。
所有采集都容错，任一环节失败记 "n/a"，不阻断评估。
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

OWUI_CONTAINER = os.environ.get("OWUI_CONTAINER", "open-webui")
PG_CONTAINER = os.environ.get("PG_CONTAINER", "pgvector")
PG_USER = os.environ.get("PG_USER", "owui")
PG_DB = os.environ.get("PG_DB", "openwebui")

# 白名单：只记这些，避免把 key / 模板全文写进仓库
CONFIG_KEYS = [
    "rag.top_k",
    "rag.top_k_reranker",
    "rag.relevance_threshold",
    "rag.reranking_model",
    "rag.embedding_model",
    "rag.embedding_engine",
    "rag.chunk_size",
    "rag.chunk_overlap",
    "rag.enable_hybrid_search",
    "rag.enable_markdown_header_text_splitter",
    "rag.external_reranker_url",
    "rag.full_context",
    "rag.bypass_embedding_and_retrieval",
    "rag.bypass_embedding_and_retrieval_for_knowledge",
    "ui.default_models",
]

# 这些即使不在白名单里也绝不记录
SECRET_HINTS = ("api_key", "password", "secret", "token")


def sh(cmd, timeout=25):
    """跑命令，失败返回 None"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout, errors="replace")
        if r.returncode != 0:
            return None
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return None


def read_config():
    """从 Open WebUI 的 SQLite config 表读关键项"""
    code = (
        "import sqlite3,json\n"
        "c=sqlite3.connect('/app/backend/data/webui.db')\n"
        "keys=%r\n"
        "out={}\n"
        "for k in keys:\n"
        "    r=c.execute('select value from config where key=?',(k,)).fetchone()\n"
        "    if r is None: continue\n"
        "    v=r[0]\n"
        "    try: v=json.loads(v)\n"
        "    except Exception: pass\n"
        "    out[k]=v\n"
        "print(json.dumps(out,ensure_ascii=False))\n"
    ) % (CONFIG_KEYS,)

    out = sh(["docker", "exec", OWUI_CONTAINER, "python", "-c", code], timeout=40)
    if not out:
        return {}
    try:
        raw = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        return {}

    # 长文本只存指纹，避免把提示词全文写进仓库
    for k, v in list(raw.items()):
        if isinstance(v, str) and len(v) > 60:
            h = hashlib.sha1(v.encode("utf-8")).hexdigest()[:8]
            raw[k] = "<%d 字符 sha:%s>" % (len(v), h)
        if any(s in k.lower() for s in SECRET_HINTS):
            raw.pop(k)
    return raw


def read_models():
    """容器内 `ollama list` 可能报错（本例即如此），走 HTTP API 更可靠"""
    import urllib.request
    base = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    info = {"installed": [], "source": "api"}
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        info["installed"] = sorted(m.get("name", "") for m in d.get("models", []))
    except Exception as e:
        info["source"] = "n/a: %s" % str(e)[:50]
    return info


def read_runtime():
    runtime = {"containers": {}, "chunks": None}
    ps = sh(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"])
    if ps:
        for line in ps.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name, image, status = parts[0], parts[1], parts[2]
            restarts = sh(["docker", "inspect", "--format", "{{.RestartCount}}", name])
            runtime["containers"][name] = {
                "image": image.split("/")[-1],
                "status": "Up" if status.startswith("Up") else status,
                "restarts": int(restarts) if (restarts or "").isdigit() else None,
            }
    # 上传是「文件级 + 知识库级」双写，count(*) 会翻倍，必须按正文去重
    n = sh(["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB,
            "-tAc", "select count(distinct text) from document_chunk"])
    if n and n.isdigit():
        runtime["chunks"] = int(n)
    raw = sh(["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB,
              "-tAc", "select count(*) from document_chunk"])
    if raw and raw.isdigit():
        runtime["chunk_rows"] = int(raw)
    return runtime


def read_code():
    commit = sh(["git", "-C", HERE, "rev-parse", "--short", "HEAD"])
    dirty = sh(["git", "-C", HERE, "status", "--porcelain"])
    return {
        "commit": commit or "n/a",
        "dirty": bool(dirty),
    }


def collect(note=""):
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "config": read_config(),
        "models": read_models(),
        "runtime": read_runtime(),
        "code": read_code(),
    }


def save(csv_path, tag="", note=""):
    """在 CSV 旁边写同名 .meta.json，失败不抛异常"""
    try:
        meta = collect(note=note)
        meta["tag"] = tag
        meta["source"] = os.path.basename(csv_path)
        out = os.path.splitext(csv_path)[0] + ".meta.json"
        io.open(out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        return out
    except Exception as e:
        print("  ! 参数快照写入失败（不影响本次结果）：%s" % str(e)[:80])
        return None


def load(csv_path):
    """读某轮 CSV 对应的 meta，没有则返回 None"""
    p = os.path.splitext(csv_path)[0] + ".meta.json"
    if not os.path.exists(p):
        return None
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except (ValueError, IOError):
        return None


def diff(a, b):
    """返回两个 meta 的参数差异：[(key, old, new)]"""
    if not a or not b:
        return []
    out = []
    ca, cb = a.get("config", {}), b.get("config", {})
    for k in sorted(set(ca) | set(cb)):
        va, vb = ca.get(k), cb.get(k)
        if va != vb:
            out.append((k, va, vb))
    return out


def main():
    ap = argparse.ArgumentParser(description="采集当前 RAG 配置快照")
    ap.add_argument("-o", "--out", help="写入文件；留空只打印")
    ap.add_argument("--note", default="", help="备注，比如 本次改了什么")
    args = ap.parse_args()

    meta = collect(note=args.note)
    text = json.dumps(meta, ensure_ascii=False, indent=2)
    if args.out:
        io.open(args.out, "w", encoding="utf-8", newline="\n").write(text + "\n")
        print("已写入 %s" % args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
