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

公网暴露、鉴权/限流/压测（阶段3）、独立前后端分离前端（阶段2）、Docker 构建修复。