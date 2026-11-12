# 本地 Linux 部署留痕（阶段1，2026-11-12）

目标：在不暴露公网的前提下，把整套服务（FastAPI 8000 + Streamlit 8501）
跑在本地 Linux 上，并从 Windows 浏览器/客户端验收。

## 环境

- WSL2 Ubuntu-24.04（虚拟盘 `E:\eng_project\Linux\Ubuntu-24.04\ext4.vhdx`，
  WSL 内为 `/dev/sdd`）；Linux 6.6、Python 3.12.3、14G 内存。
- venv `/home/wub/venvs/maotai`、pip 缓存 `/home/wub/.cache/pip`、临时 `/tmp`
  全部落在 E 盘虚拟盘内，**C 盘零写入**（安装期间该盘已用 8.0G→9.0G 为佐证）。
- 依赖：`torch==2.15.0+cpu`（pytorch CPU 索引）+ `requirements.txt`。

## 路线选择：原生 venv，而非 Docker

`.dockerignore` 排除 `data/models/`，Dockerfile 构建期须从 HuggingFace 现下
bge-small-zh-v1.5——国内网络大概率卡死。原生路线直接读 Windows 侧现成模型
（`/mnt/d/.../data/models/bge-small-zh-v1.5`，`/ready` 的 `local_files:true`
为零下载证据）。Docker 构建坑（HF_ENDPOINT 镜像或模型进构建上下文）留待后续。

## 一键启动

```bash
# WSL 内，仓库根目录
bash scripts/start_local_linux.sh
```

与 `docker/start.sh` 同构（uvicorn 8000 + streamlit 8501，host 0.0.0.0）；
Key 经 `.env` 注入（`set -a; . ./.env`），不落命令行；`setsid` 脱离 WSL 会话。

## 验收记录（真跑）

| 检查 | 结果 |
|---|---|
| `GET /health` | `status:ok`，backend cloud，index contextual |
| `GET /ready` | `ready`，1778 块，embedding `local_files:true` |
| `POST /ask`（UTF-8 body） | 6.5s，「2024年营业总收入为174,144,069,958.25元 [2024年报 P63]」 |
| `GET :8501` | 200 |
| 服务归属 | Windows `netstat` 8000/8501 同 PID（WSL localhost 转发 relay），WSL `ss -ltn` 有监听；Windows 侧无自有服务 |
| 会话存活 | wsl.exe 退出后服务仍活（setsid 生效） |

## 踩过的坑

- WSL 输出默认 UTF-16：命令前加 `WSL_UTF8=1`。
- Git Bash 的 curl 把中文参数按 GBK 传 → FastAPI 400「error parsing the body」；
  改用 Python 发 UTF-8 JSON body。
- `pkill -f "uvicorn src.api:app"` 会匹配到包含该字符串的父 shell 自身；
  用 `src[.]api` 括号写法避开自匹配。
- WSL 会话结束会清理后台子进程：nohup 不够，须 `setsid`。
- Windows 自有服务占端口时，localhost 转发先命中 Windows；验收 Linux 实例前
  先停 Windows 侧同端口服务。
- 密钥只经 `.env`/环境变量注入。本次曾误用 `pgrep -af` 打印父进程命令行导致
  Key 明文进会话记录——**该 Key 应作废轮换**；此后只用端口探活，不打进程命令行。

## 未做（后续阶段）

公网暴露、鉴权/限流/压测（阶段3）、Docker 构建修复。

## 阶段2 追加（2026-11-12）：前后端分离前端

- `web/`：React + Vite + TypeScript 手搓前端（SSE 流式问答、出处卡片、坐标高亮图、
  拒答标记、耗时/成本脚注）。14 条 vitest 组件/解析测试 + tsc + build 全绿。
- 后端新增 `GET /cite_crop?year&page&numbers`（轴4 高亮 PNG，4 条 pytest）；
  构建后 FastAPI 单端口交付：根路径服务 `web/dist`，API 路由优先（2 条 pytest）。
- 验收：浏览器真问一条，流式答出 680.93、脚注 direct·1955ms·¥0.0163、7 个出处卡片；
  展开出处坐标图真实加载 1754×72（整行横带）。隐藏视口下点按/回车不落页，
  交互经页面内 JS 触发核验，键盘路径由组件测试覆盖。
- 开发期：`cd web && npm run dev`（vite 代理 /ask、/cite_crop 到 8000）；
  交付期：`npm run build` 后由 uvicorn 单端口服务。npm 缓存经
  `npm_config_cache` 指向工作区内 `.npm-cache/`，不落 C 盘。

## 阶段3 追加（2026-11-12）：公网预备护栏与压测

- 可选 Bearer 鉴权：设 `RAG_API_TOKEN` 后 `/ask`、`/ask/stream`、`/cite_crop`
  须带 `Authorization: Bearer <token>`；`/health` 保持开放供探活。
- 可选限流：`RAG_RATE_LIMIT=N` 为每 IP 每分钟 N 次，超出 429 + `Retry-After`。
  单进程内存桶；多 worker 部署需换共享存储（Redis 等），**未做**。
- 两个变量都不设时行为与本地开发完全一致（7 条 pytest 覆盖）。
- live 验收（8002 临时实例）：无 token 401、错 token 401、对 token 200×3、
  第 4 次 429 且 `Retry-After: 60`、/health 200。
- 压测（`scripts/load_test.py`，只打免费接口 /health + 缓存 /cite_crop）：
  96 请求 / 16 并发 / **0 错误**，p50 608ms、p95 2805ms、p99 2870ms、10.75 rps
  （Windows 单 worker 同步端点线程池）。明细 `results/load_test.json`。
  /ask 压测须 `--ask N --yes`，运行前打印成本估算（默认不打，避免烧钱）。
- 公网前仍缺：HTTPS 反向代理、共享限流存储、多 worker、以及**轮换本会话
  曾误打印进记录的旧 DeepSeek Key**。