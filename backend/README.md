# Agent 后端

本地演示使用 **FastAPI + LangGraph + SQLite**。LangGraph 真正执行八个节点，使用 `SqliteSaver` 持久化 checkpoint，并通过 `interrupt()` / `Command(resume=...)` 暂停和恢复。默认推理与采集器为固定模拟实现，不需要模型密钥，不访问性能集群。

## 运行

从仓库根目录安装 `backend/requirements.txt`，再运行：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

默认数据在仓库根目录 `work/data/`。使用 `HCI_DATA_DIR` 改变存储目录。`cases.sqlite3` 保存案例、完整运行投影与追加式审计事件；`checkpoints.sqlite3` 保存 LangGraph 状态。`frontend/dist/` 存在时同一服务提供页面。此演示采用单进程与进程内锁；多实例部署需迁移到数据库事务、作业队列与远端 checkpoint。

后台启动脚本从健康接口取得实际服务 PID，并保存服务与 Windows Python 启动器的启动时间及路径。停止脚本先校验这两份身份及监听端口归属，再停止服务与启动器，避免只停止启动器后留下子进程。更新健康接口或启动脚本后需重启服务才能生成新版进程记录；旧记录通过限定于已验证启动器的进程树方式处理。

## 扩展位置

- `registry.py`：注册 `WorkflowDefinition`，以 `tool_roles` 为 fio、profile、experiment 角色指定工具，并以 `analysis_strategy` 指定分析策略。`AnalysisStrategyRegistry` 实际调用注册的函数；结果必须关联已有证据。
- `ToolDefinition`：工具可设置独立的 `source`、`mode`、`read_only`、`requires_approval`、版本及 Pydantic 输入/输出模型。返回来源与声明不符会拒绝。真实写环境工具需要接入允许列表、凭据、环境锁、执行超时、回滚与审计，并利用 `case.run_id` 作为外部幂等键。
- `providers.py`：替换 `ReasoningProvider.explain`。默认 `deterministic_demo` 明确展示模拟推理。可选 `HCI_LLM_PROVIDER=chat_completions`，同时配置 `HCI_LLM_BASE_URL`、`HCI_LLM_MODEL` 和按需配置 `HCI_LLM_API_KEY`。基址由操作者提供，例如内部兼容端点的 `/v1`；适配器追加 `/chat/completions`。该路径使用 HTTP 调用生成分析说明，不赋予模型执行命令的能力。未配置的情况下不会发起模型请求。
- `graph.py`：在八阶段图中细化领域步骤或插入子图。当前策略与单变量验证计划为示例，三条业务路线复用同一骨架；不会声称已实现后续专家尚未提供的领域方法。

运行保存 workflow、tool、strategy 与 graph 版本快照。历史运行保存每次的证据、候选、建议、实验与评价。`GET /api/cases/{case_id}/runs` 列出运行，`GET /api/cases/{case_id}/runs/{run_id}` 查看完整记录。反馈可携带 `run_id` 显式复核历史建议，事件始终追加，当前案例不会被历史复核覆盖。

## 演示流程与统计

`POST /api/cases` → `POST /api/cases/{id}/run` → 等待专家确认 → `POST /api/cases/{id}/approve` → 实验和报告 → `POST /api/cases/{id}/feedback`。

建议确认需要存在对应已完成实验。模拟实验完成不会自动将建议记为正确。正确建议占比按全部持久化运行中的最新评价计为 `confirmed / (confirmed + rejected)`，待验证不计入分母。没有已评价样本时返回 `null`。默认所有实验与指标为 MOCK，统计仅代表演示评价。

## 验证

```powershell
python -m pytest backend/tests -q
```

行为测试覆盖三条流程、实际 checkpoint 重启恢复、取消不执行、工具结果重放不重复执行、评价与统计分母、历史运行留存，以及实际注册自定义工具和分析策略。

## 版本内排查

`version_*` 是独立于旧案例演示的版本比较模块。通过 `/api/version/batches` 导入规范化测试批次，再按完整 fio 参数、协议、环境身份和来源匹配时间上最近的更早样本。`mock` 与 `measured` 相互隔离，没有上轮时返回首次记录。环境档案、批次不可覆盖；原始采集文件仅保存引用，不通过 API 读取本地路径。

比较的线程池 CPU 百分比按 `cpu_time_ms / (duration_s × 1000) × 100` 计算，允许多线程池超过 100%；每 IO 成本按池 CPU 微秒 / 同窗口 guest 完成数计算。完成数缺失或为零时每 IO 指标返回 `null`。IPC 使用池总 instructions / 池总 cycles，计数覆盖低于 90% 或未知时不比较 IPC；前后计数范围不同也不据 IPC 推断退化。网卡关联 CPU 的 softirq 是 CPU 整体值，不等于队列独占耗时。

LangGraph 依次执行配对、fio、CPU、网络、候选与归档阶段。`version_rules.py` 中的 CPU 成本、调度、NUMA、IPC、网络规则是独立可注册函数，由 `VersionRuleRegistry` 驱动；规则版本随分析保存。候选只表达相关证据和后续验证要求，不自动确认根因，不修改真实环境。

同一不可变配对重复分析复用既有记录和专家评价。补导入更近的历史批次会建立新的配对 ID，旧配对保留，新配对不会继承旧评价。反馈必须同时指定当前配对的 `analysis_id` 与 `candidate_id`，记录每次的评价、说明、专家和时间。数据库为 `version_diagnosis.sqlite3` 与 `version_checkpoints.sqlite3`，不会改写旧案例数据库。
