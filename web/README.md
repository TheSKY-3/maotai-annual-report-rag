# web —— 茅台年报问答前端（React + Vite + TypeScript）

手搓前后端分离界面：SSE 流式问答、出处卡片、轴4 坐标高亮图、拒答标记、
耗时与成本脚注。后端为仓库根目录的 FastAPI（`src/api.py`）。

```bash
npm install          # 缓存建议: npm_config_cache=<工作区>/.npm-cache
npm run dev          # 开发：vite 代理 /ask /cite_crop /health /ready 到 127.0.0.1:8000
npm test             # vitest：SSE 解析、数字提取、组件与主流程
npm run build        # 产物 dist/ 由 FastAPI 单端口服务（根路径）
```

接口约定见 `src/api.ts`；SSE 事件为 status / delta / replace / done / error。