"""
TEI -> Open WebUI 重排适配器

Open WebUI 的外部重排客户端要求返回 {"results":[{"index":..,"relevance_score":..}]}，
而 TEI 的 /rerank 返回 [{"index":..,"score":..}]。这个服务只做字段名翻译，
不加载任何模型，转发时延可以忽略。

监听 8080，转发到环境变量 TEI_URL（默认 http://reranker:80/rerank）。
"""
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEI_URL = os.environ.get("TEI_URL", "http://reranker:80/rerank")
# 传给重排模型的单块字符数。CPU 上耗时与总字符数成正比，
# 而块首就是标题和步骤开头，通常已经足够判断相关性。
DOC_CHARS = int(os.environ.get("DOC_CHARS", "500"))
# TEI 的 max-client-batch-size 上限，超过会返回 422，这里分批再合并
BATCH = int(os.environ.get("BATCH", "8"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"status": "ok", "upstream": TEI_URL})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length) or b"{}")
            query = req.get("query", "")
            docs = req.get("documents") or req.get("texts") or []
            docs = [d[:DOC_CHARS] for d in docs]

            started = time.time()
            results = []
            for start in range(0, len(docs), BATCH):
                chunk = docs[start : start + BATCH]
                body = json.dumps(
                    {"query": query, "texts": chunk, "raw_scores": False, "truncate": True}
                ).encode()
                upstream = urllib.request.Request(
                    TEI_URL, data=body, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(upstream, timeout=60) as r:
                    data = json.load(r)
                items = data if isinstance(data, list) else data.get("results", [])
                for it in items:
                    results.append(
                        {
                            "index": start + it["index"],
                            "relevance_score": it.get("score", it.get("relevance_score", 0.0)),
                        }
                    )
            print(
                "adapter: docs=%d chars=%d batches=%d tei=%.0fms"
                % (
                    len(docs),
                    sum(len(d) for d in docs),
                    (len(docs) + BATCH - 1) // BATCH if docs else 0,
                    (time.time() - started) * 1000,
                ),
                flush=True,
            )

            results.sort(key=lambda x: x["index"])
            self._send(200, {"results": results})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        print("adapter:", fmt % args, flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
