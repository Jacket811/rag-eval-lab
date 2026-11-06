# 部署说明

目标：从零把「被评估的系统」搭起来，然后能跑 `eval/run_eval.py`。

**先说清楚一件事**：这里的容器是**被评估对象**，不是这个项目本身。评估脚本在 `eval/` 里，跑在你本机，通过 HTTP 访问这些容器。

> 如果你只是想看评估结果长什么样，**[在线报告](https://jacket811.github.io/rag-eval-lab/)** 就够了，不用部署任何东西。

---

## 0. 前置

| 需要 | 说明 |
|---|---|
| Docker + Docker Compose | Windows 建议 Docker Desktop（WSL2 后端） |
| Python 3.8+ | 只跑评估和控制台，无第三方依赖 |
| 磁盘 ~20GB | 镜像 ~8GB + 模型 ~9GB + 数据 |
| 显卡（可选） | 有 NVIDIA 显卡生成快 10 倍；没有也能跑，只是慢 |

**没有显卡能不能用？** 能。把生成模型换成 `qwen3:4b` 或更小，其余不变。

---

## 1. 起容器

```bash
cp docker/docker-compose.example.yml docker-compose.yml
```

改密码（别用默认值）：

```bash
# 方式一：直接改 docker-compose.yml 里的 PG_PASSWORD
# 方式二：建 .env 文件
echo "PG_PASSWORD=你的密码" > .env
```

启动：

```bash
docker compose up -d
docker compose ps          # 等 open-webui 变成 healthy
```

启动后应该是 5 个容器：

```
ollama          11434
pgvector        5432（容器内，未映射）
open-webui      3000
reranker        8081          ← 重排模型（CPU）
rerank-adapter  8080（容器内，未映射）
```

**第一次启动会失败在 reranker 上**，因为还没准备模型——这是正常的，看下一步。

### 只想先跑通、不要重排？

删掉 `docker-compose.yml` 里的 `reranker` 和 `rerank-adapter` 两段，再把 open-webui 的 `RAG_RERANKING_ENGINE` 改成 `""`。

这样评估照样能跑，但**分数口径变成纯向量相似度**，和本仓库的实测数字不可比。

---

## 2. 拉生成与向量模型

```bash
docker exec ollama ollama pull qwen3:8b     # 生成，约 5GB
docker exec ollama ollama pull bge-m3       # 向量，约 1.2GB
```

显存紧张时（8GB 卡），建议给向量模型单独建一个 CPU 专用副本，别让它抢显存：

```bash
# 让 bge-m3 固定跑 CPU
docker exec ollama sh -c 'echo "FROM bge-m3
PARAMETER num_gpu 0" > /tmp/Modelfile && ollama create bge-m3-cpu -f /tmp/Modelfile'
```

**为什么**：8B 模型 + 向量模型同时占卡，会让 8B 掉层到 CPU，首字从 0.5 秒变成 26 秒。实测数据见[长文](../articles/8gb-vram-rag-notes.md)。

---

## 3. 准备重排模型（最容易卡住的一步）

TEI 需要一个 **ONNX 格式**的 `bge-reranker-v2-m3`，放在 `./models/bge-reranker-v2-m3-onnx/`。

### 3.1 目录结构（必须是这样）

```
models/bge-reranker-v2-m3-onnx/
├── config.json
├── tokenizer.json
├── tokenizer_config.json
├── special_tokens_map.json
├── sentencepiece.bpe.model
└── onnx/
    └── model.onnx          ← 约 570MB（fp32 会是 2.2GB 左右）
```

### 3.2 导出

```bash
pip install "optimum[onnxruntime]"
optimum-cli export onnx --model BAAI/bge-reranker-v2-m3 --task text-classification ./models/bge-reranker-v2-m3-onnx
```

导出来是 fp32，约 2.2GB，能跑但慢（每条约 1 秒）。想快就量化：

```python
from onnxruntime.quantization import quantize_dynamic, QuantType
quantize_dynamic("./models/bge-reranker-v2-m3-onnx/model.onnx",
                 "./models/bge-reranker-v2-m3-onnx/onnx/model.onnx",
                 weight_type=QuantType.QInt8)
```

> **说明**：上面的导出命令是参考路径，optimum 版本不同参数可能略有差异。
> 量化后的 `model.onnx` 应该在 500–600MB；如果你导出的是 2GB 多，那就是没量化，能跑但每条要多花约 1 秒。如果你导出后 TEI 报错，先确认 `config.json` 里 `architectures` 是 `XLMRobertaForSequenceClassification`。

### 3.3 启动并验证

```bash
docker compose up -d reranker rerank-adapter
docker logs reranker --tail 20        # 应该看到 "Ready"
curl http://127.0.0.1:8081/health     # TEI 健康检查
```

直接测一次重排：

```bash
curl -s http://127.0.0.1:8081/rerank \
  -H "Content-Type: application/json" \
  -d '{"query":"怎么改密码","texts":["修改登录密码的步骤","如何安装打印机"],"truncate":true}'
```

预期返回两条，第一条分数明显更高：

```json
[{"index": 0, "score": 0.9338773}, {"index": 1, "score": 0.0000201}]
```

**注意看字段名是 `score`**——Open WebUI 要的是 `relevance_score`。名字对不上不会报错，只会让重排结果静默失效，这就是下面 adapter 存在的原因。

**别直连 TEI，一定走 adapter**：它是唯一把 `score` 翻译成 `relevance_score` 的地方。跳过它，重排等于没做，而且不报错。

---

## 4. 建知识库

1. 打开 http://127.0.0.1:3000，注册第一个账号（第一个用户自动是管理员）
2. 左上「工作空间」→「知识库」→ 新建
3. 上传你的文档（md / txt / pdf 都行）
4. **等向量化完成**（文档状态从「处理中」变成「已完成」）
5. 从地址栏拿集合 ID：

```
http://127.0.0.1:3000/workspace/knowledge/8339ed09-be5a-46ec-9877-df93e0f771ee
                                          └──── 这一段就是 collection ────┘
```

---

## 5. 设置检索参数

**这里有个大坑**：参数有三个来源，优先级从低到高：

```
① docker-compose 的 environment     ← 只在第一次启动时写入数据库，之后改了没用
② 数据库 config 表                   ← 真正生效的地方
③ UI 管理员设置                       ← 本质也是写数据库
```

**改了 compose 里的 `RAG_TOP_K` 却没生效？** 因为数据库里已经有值了，环境变量被覆盖。

### 本仓库实测用的配置

| 参数 | 值 | 在哪改 |
|---|---|---|
| 分块大小 `rag.chunk_size` | 500 | 管理员设置 → 文档 |
| 分块重叠 `rag.chunk_overlap` | 50 | 同上 |
| 向量模型 `rag.embedding_model` | `bge-m3-cpu` | 同上 |
| 混合检索 `rag.enable_hybrid_search` | 开 | 同上 |
| 粗召回 `rag.top_k` | 12 | 同上 |
| 送模型段数 `rag.top_k_reranker` | 4 | 同上 |
| 相关度阈值 `rag.relevance_threshold` | 0.15 | 同上 |
| 查询改写 | 关 | 管理员设置 → 界面 |

### 想直接改数据库

```bash
docker exec open-webui python -c "
import sqlite3, time
c = sqlite3.connect('/app/backend/data/webui.db')
for k, v in [('rag.top_k','12'), ('rag.top_k_reranker','4'), ('rag.relevance_threshold','0.15')]:
    c.execute('update config set value=?, updated_at=? where key=?', (v, int(time.time()), k))
c.commit()
for r in c.execute(\"select key,value from config where key like 'rag.top_k%' or key='rag.relevance_threshold'\"):
    print(r)
"
docker restart open-webui      # 有些参数启动时才读，必须重启
```

**改完一定重启，并用 `python eval/snapshot.py` 确认真的变了。**

---

## 6. 让评估脚本指向你的系统

改 `eval/questions.json` 顶部的集合 ID：

```json
"meta": { "collection": "你的集合ID" }
```

然后跑：

```bash
cd eval
python run_eval.py --validate     # 先确认题库要点在库里
python run_eval.py --run --tag 第一轮
```

详细用法见 [README 的「快速开始」](../README.md#快速开始)。

---

## 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| reranker 反复重启 | 模型目录结构不对 | 确认 `onnx/model.onnx` 存在，`config.json` 同级 |
| 重排返回 422 | 一次送太多条 | 减小 `BATCH`，必须 ≤ TEI 的 `--max-client-batch-size` |
| 重排分数全是 0 | 直连了 TEI，字段名不匹配 | 必须走 `rerank-adapter:8080` |
| 改了参数没效果 | 数据库覆盖了环境变量 | 改 config 表 + 重启 |
| 评估全 0 分 | collection ID 不对 | 从知识库页面地址栏重新复制 |
| 首字特别慢（20 秒+） | 向量模型和生成模型抢显存 | 给向量模型设 `num_gpu 0` |
| Docker 重启后数据没了 | 挂载盘没重新共享（Windows + 非 NTFS） | 检查数据卷，别用 exFAT 盘做 bind mount |

---

## 一个提醒

这套配置是为「8GB 显存的笔记本」调的，很多默认值（top_k=12、kr=4、阈值 0.15）**跟你的知识库规模和文档写法有关**，不能直接照搬。

**先跑 `--validate` 和一轮基线，再决定改什么。** 换领域要重测的项，见 README 的「换一个知识库」一节。
