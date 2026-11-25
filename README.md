# 隧道代理 IP 质量测试 WebUI

测试不同隧道代理厂商的出口 IP 质量：同一脚本统一打 `https://ifconfig.me/ip`，
并行采样 1~2 小时，自动计算 **去重数 / 重复率 / /24 集中度 / 跨家交集**，
数据落本地 SQLite。

## 快速开始

双击 `start.bat`（首次自动创建 .venv 并装依赖；若旧实例还在跑会**先自动结束它再启动**，
改完代码双击即可生效），或手动：

```bash
uv venv .venv
uv pip install -r requirements.txt --python .venv/Scripts/python.exe
.venv/Scripts/python.exe -m app.main
```

打开 <http://127.0.0.1:8821> （host/port 在 `.env` 里改）。

## Docker 部署

```bash
docker compose up -d --build   # 构建并启动；改代码后重新执行即完成滚动更新
docker compose down            # 停止（数据保留在 ./data）
```

- 宿主机端口读 `.env` 的 `WEBUI_PORT`（默认 8821），容器内固定监听 8821
- SQLite 持久化在 bind mount `./data/`，删容器不丢数据
- 容器内置 `TZ=Asia/Shanghai`（采样时间戳与本地一致）、非 root 用户运行、自带 healthcheck
- 换 pip 源：`docker compose build --build-arg PIP_INDEX_URL=<url>`

## 使用流程

1. **厂商管理** → 新增厂商：填隧道地址 host:port + 账密（或直接贴完整代理串，
   支持 `http://user:pass@host:port` 与 `host:port:user:pass` 两种形态）；
   点「测试」经隧道真实取一次出口 IP 验证连通。
2. **测试任务** → 点选要对比的多个厂商 chip → 设运行时长（分钟，到时自动停止）、
   采样间隔（秒/次）、超时 → 启动。选多家=同时并行采样，才能算跨家交集。
3. **运行监控** → 实时进度条、采样数、去重 IP、最近出口；可手动停止。
4. **对比分析** → 各厂商指标表 + 跨家交集矩阵 + 采样明细 + CSV 导出。

## 指标口径（权威实现见 `app/stats.py`）

| 指标 | 口径 |
|---|---|
| 去重数 | 成功采样出口 IP 的去重个数 |
| 重复率 | `1 − 去重IP数 ÷ 成功采样数`，越高=出口越不分散 |
| /24 集中度 | 去重 /24 网段数 + Top /24 占比（占比越高=越集中在少数段） |
| 跨家交集 | `共同去重IP数 ÷ 较小方去重IP数 × 100%`；**< 15% 判「真不太相同」**（绿），15~40% 黄，>40% 红（大概率同源池）。矩阵同时给 /24 级交集作参考 |

## 设计要点

- **出口保真**：探测会话 `trust_env=False`，不吃系统代理——测出来的就是隧道出口，
  不会被本机代理污染（复用自 wenxin 生态实测结论）。
- **每请求全新连接**：隧道网关普遍按 TCP 连接绑定出口 IP，keep-alive 复用会让同一连接
  上的请求反复命中同一出口，把「每请求换 IP」测成「从不换 IP」（2026-11-25 实测：
  KDL 共享连接 1/6 唯一 vs 全新连接 6/6 唯一；巨量同理）。因此每次探测都新开
  Session，`延迟 ms` 包含隧道建连成本，属真实单请求口径。
- **口径一致**：所有厂商共用同一目标端点与兜底端点列表（主端点失败时才依次尝试
  `api.ipify.org`），采样记录实际命中的端点。
- **计费提醒**：默认 5s 间隔 ≈ 720 次请求/小时/厂商，跑之前确认套餐的请求频率规格；
  需要省预算就把间隔调大。
- **IPv6-only 出口**记为失败（本项目指标按 IPv4 /24 口径设计）。
- 服务重启后，上次遗留的 running 任务自动标记 `interrupted`。

## 数据

SQLite：`data/app.db`（WAL 模式）。表：`vendors`（厂商配置）、`runs`（任务）、
`run_vendors`（任务-厂商）、`samples`（逐次采样：时间/出口IP/延迟/错误/端点）。
厂商可增删改；删除厂商保留其历史采样记录。

## API 速览

```
GET/POST        /api/vendors          厂商列表 / 新增
PUT/DELETE      /api/vendors/{id}     编辑 / 删除
POST            /api/vendors/{id}/test  单次连通测试（经隧道取出口 IP）
GET/POST        /api/runs             任务列表 / 创建并启动
GET             /api/runs/{id}        运行详情（实时）
POST/DELETE     /api/runs/{id}/stop|  手动停止 / 删除任务
POST            /api/runs/batch-delete     批量删除任务（含明细，运行中拒删）
GET             /api/runs/{id}/summary     指标 + 交集矩阵
GET             /api/runs/{id}/samples?vendor_id=  采样明细
GET             /api/runs/{id}/export?vendor_id=   CSV 导出
```
