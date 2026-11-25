# proxy-check-webui — 隧道代理 IP 质量测试

FastAPI + 原生 HTML 单页（无构建、无运行时外网依赖）。项目细则：

## 指标口径（唯一权威 = app/stats.py）

- 重复率 = 1 − 去重IP ÷ 成功采样
- /24 集中度 = 去重 /24 段数 + Top /24 占比（按成功采样数加权）
- 跨家交集% = 共同去重IP ÷ 较小方去重IP；<15% 判「真不太相同」，阈值前端可调
- 探测统一打 ifconfig.me/ip（curl UA 拿纯文本），失败才按序落兜底端点 api.ipify.org；
  采样记录实际 endpoint，保证对比口径一致

## 硬约束（改代码前必读）

- 探测会话必须 `trust_env=False`（proxy.probe_ip 内每次新建）：本机常驻系统代理，
  吃了 env 代理「隧道出口」就变成本机代理出口，数据全错
- 探测必须**每请求全新 Session**，禁止改回共享 keep-alive：隧道按 TCP 连接绑出口，
  复用连接会同 IP 假象（2026-11-25 A/B 实测 KDL 1/6 vs 6/6）
- 采样线程 per-vendor 串行（RunManager），sqlite 每操作独立短连接 + WAL，
  不要引入共享连接跨线程
- 服务重启时 running 任务统一标 interrupted（runner.recover_interrupted），
  别把悬挂 running 当活任务
- 采样线程顺序 = 请求→落库→sleep 到下一间隔（deadline 感知），到时自动停；
  手动停止与自然结束在 _finalize 区分 stopped/completed

## 风格与质量门槛

- Python 遵循 python-code-style 技能四则（复杂实参先装载、SQL 占位符按列数生成等）
- 交付前：`.venv/Scripts/python.exe -m py_compile app/*.py` +
  `uvx ruff@latest check --select F,E9,W6 .` + `node --check static/app.js`
- 冒烟路径：POST /api/vendors → POST /api/vendors/{id}/test（真实出口 IP）→
  POST /api/runs（duration_minutes 0.3 = 18 秒短跑）→ GET summary 看指标与矩阵

## 坑（实测）

- 换代码重启 = 双击 start.bat：它按 .env 端口 findstr LISTENING 找旧实例自动 taskkill；
  不要手动找 PID（本机 git-bash 会吞 taskkill 的 //参数，杀进程用 PowerShell 或 bat）
- 起第二个实例做验证时务必设 WEBUI_DB 指向隔离库：共享生产库会让它的
  recover_interrupted 把正在跑的任务误标 interrupted
- UI 里 /api/vendors/{id}/test 必须 POST（曾因 fetch 默认 GET 报 405）
- git-bash 内联 curl -d 'json' 会被吞引号 → 用 --data-binary @file
- 供应商连通失败先区分：407=账密/白名单问题、批量 curl(7)=订单失效或超并发
