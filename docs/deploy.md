# 从零部署

**不需要懂 Docker，不需要用过命令行。** 照着点就行，全程约 1 小时（大半时间在等下载）。

> 只想看看效果、不想装任何东西？打开 **[在线报告](https://jacket811.github.io/rag-eval-lab/)** 就够了。

---

## 这套东西由四部分组成

先搞清楚要装什么，否则容易懵。

```
① Docker      装东西的「容器」，把软件打包成一个个盒子
② Open WebUI  网页界面，浏览器里提问、传文档
③ Ollama      模型运行器，跑 AI 模型的
④ 评估工具    这个项目的脚本，测上面那套答得准不准
```

①②③ 用 Docker 装，④ 直接双击运行。

---

## 你需要准备

| 项目 | 要求 | 达不到怎么办 |
|---|---|---|
| 操作系统 | Windows 10/11 或 macOS | — |
| 硬盘空间 | **35GB** 以上 | 模型占 9GB，别放满的盘 |
| 内存 | 16GB 以上 | 8GB 也能跑，会很慢 |
| 显卡 | 可选 | 没有也能用，只是回答慢 |
| 时间 | 约 1 小时 | 大半在等下载 |

---

## 第 1 步：装 Docker Desktop（约 15 分钟）

Docker 是运行这套系统的底座。**没装过就照下面做。**

### 1.1 下载

打开 https://www.docker.com/products/docker-desktop/

点 **Download for Windows**（Mac 选 Apple Chip 或 Intel Chip，看你电脑）。

### 1.2 安装

双击下载的 `Docker Desktop Installer.exe`：

- 一路点 **Next / OK**
- 遇到 **"Use WSL 2 instead of Hyper-V"** 打勾，点 OK
- 完成后提示重启，**点重启**

### 1.3 启动

重启后从开始菜单打开 **Docker Desktop**。

**你会看到**：右下角托盘出现一个小鲸鱼图标，一直在动。

**等它不动了**（约 1-2 分钟），说明启动完成。界面左下角会显示绿色的 **Engine running**。

> ⚠️ **鲸鱼还在动就别急着下一步**，Docker 没就绪时所有命令都会失败。

### 1.4 验证

按 `Win + R`，输入 `cmd`，回车。在黑色窗口里粘贴：

```
docker --version
```

**你应该看到**类似：

```
Docker version 27.3.1, build ce12230
```

看到版本号就成功了。看不到就回到 1.3 检查鲸鱼图标。

<details>
<summary>装不上 / 报错的常见原因（点开）</summary>

| 报错 | 原因 | 解决 |
|---|---|---|
| `WSL 2 installation is incomplete` | 缺 WSL2 | 按提示点链接下载安装，或管理员 PowerShell 执行 `wsl --install` |
| `Virtualization support not detected` | BIOS 没开虚拟化 | 重启进 BIOS，找 `Intel VT-x` / `AMD-V`，设为 Enabled |
| 安装卡住 | 需要重启 | 重启后重装 |

</details>

---

## 第 2 步：下载项目（约 2 分钟）

1. 打开项目主页 https://github.com/Jacket811/rag-eval-lab
2. 点绿色的 **Code** 按钮 → **Download ZIP**
3. 解压到一个**路径不含中文和空格**的位置，例如：

```
D:\rag-eval
```

**你会看到**这些文件：

```
setup.bat          ← 双击它开始部署
start.bat          ← 以后双击它用评估工具
docker\            容器配置
eval\              评估脚本
docs\              说明文档
```

> ⚠️ 别放在 `桌面`、`下载` 里带中文的路径下，有些工具会因此报错。

---

## 第 3 步：一键部署（约 30 分钟，主要是等下载）

**双击 `setup.bat`。**

它会自动做三件事：

```
[√] 已安装 Docker
[√] Docker 正在运行
[√] 已生成 docker-compose.yml
第 1 步：下载镜像并启动容器
...
第 2 步：下载模型（约 6GB）
...
部署完成
```

**过程中会滚动大量进度条和 JSON 文本，这是正常的**，去喝杯水。

> 下载模型那一步屏幕会刷出 `{"status":"pulling..."}` 这样的 JSON，看着像报错，其实是在下载。

### 完成后应该是这样

回到黑色窗口（或重新开一个），粘贴：

```
docker ps
```

**你应该看到 3 个容器**，状态都是 `Up`：

```
ollama        Up 5 minutes
pgvector      Up 5 minutes
open-webui    Up 2 minutes (healthy)
```

然后看模型有没有下好：

```
docker exec ollama ollama list
```

> 如果这条报 `something went wrong`，是容器里的 ollama 命令行有问题（服务本身正常）。
> 换成：`curl http://127.0.0.1:11434/api/tags`，能看到 `qwen3:8b` 和 `bge-m3` 就说明下好了。

**你应该看到**：

```
NAME            SIZE
bge-m3:latest   1.2 GB
qwen3:8b        5.2 GB
```

<details>
<summary>setup.bat 报错怎么办（点开）</summary>

| 现象 | 原因 | 解决 |
|---|---|---|
| `没有找到 Docker` | 没装或没加到 PATH | 重装 Docker Desktop，安装时勾选 Add to PATH |
| `Docker 已安装，但没有运行` | Docker Desktop 还没启动完 | 打开它，等鲸鱼图标不动 |
| 卡在 `Pulling` 很久 | 网络慢 | 配镜像加速，见下方 |
| `port is already allocated` | 3000 端口被占用 | 关掉其他 Open WebUI，或改 compose 里的端口 |

**配镜像加速**（国内网络强烈建议）：

Docker Desktop → 右上角齿轮 **Settings** → **Docker Engine**，把内容改成：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com"
  ]
}
```

点 **Apply & restart**。

</details>

---

## 第 4 步：打开网页、注册账号（约 3 分钟）

1. 浏览器打开 **http://127.0.0.1:3000**
2. 点 **Sign up**（注册）
3. 随便填个名字、邮箱、密码

> **第一个注册的账号自动就是管理员**，这个很重要——后面建知识库需要管理员权限。

**你会看到**聊天界面，中间有个输入框写着「有什么我能帮您的吗？」

试着问一句「你好」，如果能回答，说明模型通了。

---

## 第 5 步：建知识库、传文档（约 5 分钟）

1. 左侧点 **工作空间**
2. 点 **知识库** → 右上角 **+** 新建
3. 起个名字，比如「我的文档」
4. 进入知识库，点 **上传文件**，选你的文档（pdf / md / txt / docx 都行）
5. **等状态从「处理中」变成「已完成」**

> ⏳ 文档多的话要等几分钟，向量化很吃 CPU。没变绿之前，检索是空的。

### 复制知识库 ID

上传完，看浏览器地址栏，是这样一串：

```
http://127.0.0.1:3000/workspace/knowledge/8339ed09-be5a-46ec-9877-df93e0f771ee
                                          └────────── 复制这一段 ──────────┘
```

**把最后那串字符复制下来**，下一步要用。

---

## 第 6 步：告诉评估工具「你的知识库是哪個」（约 1 分钟）

1. 打开文件夹 `D:\rag-eval\eval`
2. 右键 `questions.json` → **打开方式** → **记事本**
3. 最上面几行长这样：

```json
"meta": {
    "collection": "8339ed09-be5a-46ec-9877-df93e0f771ee",
    ...
}
```

4. 把引号里那串，**替换成你刚才复制的**
5. 保存，关掉

> ⚠️ 这一步不做，评估会全部 0 分——因为它还在查别人的知识库。

---

## 第 7 步：跑第一次评估（约 5 分钟）

**双击 `start.bat`。**

浏览器会自动打开一个控制台页面：

```
┌──────────────────────────────────────┐
│ 环境                                  │
│  ● 容器   open-webui / pgvector / ollama │
│  ● 题库   70 题                        │
│  ● 登录凭证 已能取到 token              │
│                                      │
│ 跑一轮评估                             │
│ 标签 [基线__________]                  │
│ [ 开始评估 ]                          │
└──────────────────────────────────────┘
```

1. 标签里填 `基线`（意思是「改动前的状态」）
2. 点 **开始评估**
3. 看日志一行行刷出来，约 5 分钟

**跑完会显示**：`完成。点击右侧「打开对比报告」查看…`

点「打开对比报告」，你会看到分类命中率、逐题散点、错题列表。

> **第一次跑出来分数低是正常的。** 你的文档写法、题目、参数都还没调过。

---

## 第 8 步（可选）：改个参数试试

这是这套工具最有用的地方——**改完能立刻看见效果**。

1. 控制台中间有「检索参数」卡片
2. 把 **送模型段数 k_reranker** 从 `4` 改成 `6`
3. 点 **保存并重启**（等约 20 秒）
4. 回到上面，标签填 `kr6`，再点 **开始评估**
5. 跑完打开报告，顶部两个下拉框选「基线 → kr6」

**你会看到**：

```
当时的配置
─────────────────────────
参数                  基线   kr6
rag.top_k_reranker      4     6
─────────────────────────
```

和下面的分数变化、哪几道题变了、变好还是变坏。

**这就叫「用数据决定该不该改」。**

---

## 进阶：加上重排（可选，较难）

第 3 步的简化配置**没有重排**，所以分数只能跟你自己比，不能跟本仓库 README 里的数字比。

想要完整链路（和文章里的实测一致），需要额外准备一个重排模型：

1. 用 `docker/docker-compose.example.yml` 替换当前的 `docker-compose.yml`
2. 准备 ONNX 格式的 `bge-reranker-v2-m3` 放到 `models/bge-reranker-v2-m3-onnx/`
3. 重启容器

这一步涉及 Python 环境和模型转换，**建议先跑通前面的流程再来**。详见文末「完整配置」。

<details>
<summary>完整的 5 容器配置说明（点开）</summary>

`docker/docker-compose.example.yml` 比简化版多的两个服务：

| 服务 | 作用 |
|---|---|
| `reranker` | TEI，跑重排模型（CPU） |
| `rerank-adapter` | 把 TEI 返回的 `score` 转成 Open WebUI 要的 `relevance_score` |

重排模型准备：

```bash
pip install "optimum[onnxruntime]"
optimum-cli export onnx --model BAAI/bge-reranker-v2-m3 --task text-classification ./models/bge-reranker-v2-m3-onnx
```

导出后目录应该是：

```
models/bge-reranker-v2-m3-onnx/
├── config.json
├── tokenizer.json
├── ...
└── onnx/
    └── model.onnx      ← 约 570MB（fp32 会是 2.2GB）
```

验证重排是否工作：

```bash
curl -s http://127.0.0.1:8081/rerank -H "Content-Type: application/json" \
  -d '{"query":"怎么改密码","texts":["修改密码的步骤","如何装打印机"],"truncate":true}'
```

预期返回 `[{"index":0,"score":0.93},...]`，第一条分数明显更高。

</details>

---

## 常见问题

**Q：关掉黑窗口，服务会停吗？**
不会。容器在后台跑。要停：在项目目录执行 `docker compose down`。

**Q：电脑重启后还要重新装吗？**
不用。Docker Desktop 设成开机自启（Settings → General → Start Docker Desktop when you sign in），容器设了 `restart: unless-stopped`，会自动起来。

**Q：评估全是 0 分？**
第 6 步的知识库 ID 没改，或者文档还没向量化完。

**Q：回答特别慢（20 秒以上）？**
向量模型和生成模型抢显存了。控制台里把生成模型换成更小的（`qwen3:4b`），或者参考[调参手册](tuning.md)。

**Q：改了参数没效果？**
必须点「保存并重启」，参数有些是启动时才读的。

**Q：数据存在哪？会上传吗？**
全部在你本机。Docker 卷里，不出网。

---

## 下一步

- 参数怎么调、哪些别动 → [调参手册](tuning.md)
- 为什么这么设计、8GB 显存的账怎么算 → [长文](../articles/8gb-vram-rag-notes.md)
- 题库怎么写、换一个知识库要改什么 → [README](../README.md#换一个知识库)
