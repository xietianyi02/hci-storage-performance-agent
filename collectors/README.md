# Linux 采集与导入适配器

这是可执行的本机 Linux 采集入口，只使用 Python 标准库。它观察外部调度的 fullfio 测试，读取 `/proc`、`/sys` 并调用固定参数的 `perf stat` / `perf record`。不会连接 SSH、启动 fio、改变线程亲和性或调优环境。

当前开发电脑是 Windows，已验证纯解析、归一化、合并与 Linux 平台拒绝逻辑；尚未在真实 HCI 主机验证 perf 权限、内核兼容性或采集开销。合并命令与解析测试可在 Windows 运行。

## 一轮采集

从仓库根目录运行；Linux 主机需要 Python 3.11+。`perf` 可选，缺失或无权限时记录失败原因，计数保持缺失。

```bash
python3 -m collectors collect --help

# 每台主机分别执行，run-id 与场景元数据一致。
python3 -m collectors collect \
  --run-id fullfio-20260929-001 --host-role local \
  --warmup 30 --duration 60 --interval 3 --record-seconds 5 \
  --scenario-json scenario.json --out local.json
```

guest 使用 `--host-role guest`，远端数据主机使用 `--host-role remote`。guest 可使用 `--fio-json /path/to/existing-fio.json` 关联已有 fio 结果，也可等测试结束后在合并时提供。输出已有同名文件时拒绝覆盖。

CLI 默认观察 30 秒预热和 60 秒正式测量，以 3 秒为采样间隔：预热 10 窗、正式 20 窗。每窗都保留，整轮线程池、CPU、网卡摘要只统计正式测量。`--duration` 只表示正式阶段，`--warmup 0` 可接入已有只采稳态的任务。Python `collect()` 为兼容旧调用默认 warmup=0；CLI 默认 warmup=30。

已有调度器应让三端观察从 fio 预热起点开始。可用 `--start-at 2026-09-29T01:00:00+00:00` 约定未来起点，但仍需要外部时钟同步；采集包记录实际开始和结束时间。它不会证明三端 NTP/PTP 同步精度。轮询使用绝对 monotonic deadline，避免每次读文件后再睡 3 秒累计漂移；raw bundle 同时保留请求时长与实际端点时长，归一化 observation 使用实际预热边界和正式阶段时长，末窗不强行写成 3 秒。

默认按进程名发现 guest fio、本地 QEMU/kvm、两端 asan-stord 与 glusterfsd，每次轮询采集全部匹配线程，包括低 CPU 的等待线程。轮询间隔内创建又退出的短生命周期线程可能遗漏，质量提示保留这一限制。线程池由名称匹配，PID/TID/starttime 用于本轮生存期识别。进程命令行不写入采集包。

发现规则可通过 `--roles-json` 替换，例如：

```json
[
  {"pattern": "^asan-stord$", "role": "stord", "hosts": ["local", "remote"]},
  {"pattern": "^glusterfsd$", "role": "glusterfsd", "hosts": ["local", "remote"]}
]
```

规则依据进程 `comm`，默认无法区分 DATA、ARBITER 或卷实例，质量提示会保留这种歧义。需要实例级映射时应扩展 `ProcReader.discover` 的身份适配器，不能仅凭 `glusterfsd` 名称推断数据副本角色。

## 收集的数据

- 动态线程清单、累计用户/内核 CPU ticks、状态、schedstat、上下文切换、迁移计数、有效 CPU/内存允许列表。
- 同一线程池正式阶段的 CPU 时间、指令和周期计数；线程创建或退出造成覆盖不完整时，对应总量为 null。
- 每窗重新执行 `perf stat --per-thread -e '{instructions,cycles}'`，挂接该窗起点发现的 TIDs；保留原始未缩放计数、running 百分比、启用/运行时间估计、命令和权限错误。新生线程到下一窗才挂接。只在同一 PID/TID/starttime 的两端都被观察且 perf 起止与 proc 窗口各端相差不超过 0.1 秒时归一化计数；不会把整轮 counter 或 IPC 拷贝到每点。
- 每窗单线程 PID/TID/starttime、线程名和状态、起点／终点最后执行 CPU、有效 CPU 允许列表、NUMA、CPU 占用率、迁移数和 runqueue 等待。CPU 是采样端点最后运行位置，3 秒内曾在哪些 CPU 驻留仍未知；新生／退出线程的区间消耗与未观察端点保留 null。
- 短窗口 99 Hz `cpu-clock` 栈采样原始文件；初始 TID 集合之外的新线程不自动加入 perf。记录窗口较短，只用于后续热点检查，不能据此产生整轮每 IO 指令成本。
- 每逻辑 CPU 的 `/proc/stat` 差值、IRQ 与 softIRQ 次数差值；网卡接口包数、字节、丢包差值和实际观察时长计算的速率。
- CPU/网卡 NUMA 节点、SMT 拓扑（siblings 含当前 CPU）、core/socket ID、同窗每逻辑 CPU busy/IRQ/softIRQ、频率、网卡 IRQ 配置与实际落核、RPS/XPS 队列配置，以及进程级 NUMA 驻留页。SMT 兄弟同窗忙度用于观察共享物理核负载，不自动判为性能下降根因。

CPU 读取覆盖 `/proc/stat` 中全部逻辑 CPU，独立于目标线程发现结果、线程允许列表和负载；空闲或没有目标线程的 CPU 同样保留有效的分窗差值。真正空闲（idle ticks 有增长）的 CPU 可产生 busy=0、idle=100；缺端点、计数回退、全部 ticks 不增长的 CPU 无法算百分比，省略该窗行，不能生成 0% 假值。

归一化 Snapshot 增加向后兼容的 `cpu_inventory`，每项为 `{host_role, cpu_id, numa_node, socket_id, core_id, smt_sibling_cpu_ids}`。清单是该轮 `/proc/stat` 观察到的 ID 与明确 `/sys/devices/system/cpu/online` 清单的并集，保留 NUMA、socket/core 和完整 SMT siblings（含自身）；单独的 sys CPU 目录可能包含离线 CPU，因此不能据目录数量宣称在线数量。清单允许页面保留缺失 CPU 的位置，并将缺行显示为未知。它不证明每个 CPU 在每个窗都在线；热插拔或读取失败都可能产生缺行。

旧记录没有清单时默认 `[]`，其全 CPU 范围未知；旧采集包缺完整 proc 或 sys online 证据时保留 `CPU_INVENTORY_RANGE_UNKNOWN` 提示。`CPU_INCOMPLETE_WINDOWS` 提示清单中存在缺失分窗 CPU。不能从上一轮 CPU 范围或某个线程的亲和 CPU 列表补造本轮数据。

不支持或未启用的数据保持未知：schedstats 关闭时 runnable 等待不是 0；产品软亲和配置、真实远端内存访问比例、阻塞栈、竞争进程归因、队列独占 softIRQ 耗时和 TCP 重传归因均未实现。采集不会自动启用内核选项。

完整 `perf.data` 和 JSON 原始文件位于 `<输出名>.artifacts/`；导入只保留引用，API 不读取任意文件路径。当前归一化 `top_functions` 为空，需后续接入真实 `perf report` 解析适配器。

IPC 使用该线程该窗 instructions / cycles，前端只在 counting_ratio ≥ 0.9、cycles > 0 且 scope 明确时展示。counting_ratio 是 PMU running/enabled 的复用覆盖，不能用线程启用时间 / 3 秒 wallclock 取代；低 CPU 或休眠本身不使有效 IPC 失效。对齐校验单独检查 perf 起止时间。单线程 CPU 百分比因 ticks 量化或读取时序超过 100% 时保留未知，原始 ticks 仍可追溯。

每 3 秒重新挂接 perf 有启动、读取及 PMU 开销，可能扰动性能；当前不是零开销跟踪。真实接入时需验证目标 TID 规模、采集扰动、内核/perf 版本、线程新生覆盖与时钟精度。短生命周期线程仍可能被轮询遗漏。各层时延／队列深度未实现产品采集器；`sample_windows[].layers` 预留 `{layer, scope, latency_us, queue_depth}`，当前为空。

## 合并为可导入批次

准备场景文件 `scenario.json`：

```json
{
  "id": "nfs-4k-write-qd512",
  "title": "NFS 4K 随机写",
  "protocol": "nfs",
  "fio": {"rw": "randwrite", "bs": "4k", "iodepth": 64, "numjobs": 8, "vm_count": 1, "ioengine": "libaio", "direct": 1}
}
```

准备批次元数据 `batch.json`，环境档案是运营者提供的稳定环境定义；负载、PID/TID 不作为环境 ID。相同环境的后续批次应复用相同档案，仅更新批次 ID、构建标识、变更与时间。

```json
{
  "id": "fullfio-20260929-001",
  "title": "本轮 fullfio",
  "build": "build-candidate",
  "environment": {"id": "poc-lab-01", "label": "POC 环境 01", "description": "填写稳定硬件、节点与虚拟机配置档案"},
  "changes": ["填写本轮实际变更"]
}
```

测试调度器可输出 `fio-window.json`，保存**正式测量窗口实际完成的 guest IO 数**与实测起止时间：`{started_at, ended_at, completed_ios}`。它从预热结束算起，不包括前 30 秒。不能用包含其他阶段的整份 fio JSON `total_ios` 代替采集子窗口完成数。

还应从 guest fio 分窗计数／日志生成 `fio-samples.json`：有序 JSON 数组，每项仅含 `{start_offset_s, end_offset_s, completed_ios, latency_ms}`，offset 相对含预热的整个 fio 起点，单位秒；latency_ms 可为 null。正式第一个完整窗通常为 30～33 秒。completed_ios 必须来自该窗实际完成数；不能根据整轮平均 IOPS 造点。可提供预热与正式全部窗口，也可只提供有实数的窗口；未提供的窗 IOPS／时延为 null。

```bash
python3 -m collectors merge \
  --bundle guest.json --bundle local.json --bundle remote.json \
  --batch-json batch.json --scenario-json scenario.json \
  --fio-json existing-fio.json --fio-window fio-window.json \
  --fio-samples-json fio-samples.json \
  --out import-batch.json
```

合并拒绝不同 run_id、重复主机角色、场景模型不一致、窗口数量／phase／offset／起止时间不一致或 fio 参数矛盾。三端合并以 guest（缺失时第一个包）的实际 offset 为参考，并保留各端基于自身实际时长计算的 CPU 与网卡速率。默认时序容差为 0.1 秒，运营者可通过 `--tolerance` 指定已确认的同步误差；PMU 端点校验仍固定为 0.1 秒。质量记录保留外部时钟依赖。

时序批次的 fio JSON 必须是正式测量阶段摘要，其 direction runtime 与实测正式时长需在 1% 或指定容差内，包含预热的 90 秒摘要会被拒绝。正式 20 窗完成数齐全时自动求和作为整轮 denominator；同时提供 fio-window 时要求完成数一致。IOPS 点使用 guest 完成数 / 实际参考窗长，不复制 fio 摘要。

未提供 `--fio-window` 且正式分窗完成数不全时允许部分导入，但 `window.completed_ios` 为 null，所有每 IO 成本保持未知。页面可查看 CPU/perf 与 fio 正式阶段摘要；没有分窗 guest 输入时 IOPS 点保持未知。旧 collector-schema 1 包仍可合并，但不把其整轮摘要转换为新的时序点。PMU 窗口与 guest 计数窗口不一致时也禁用每 IO 成本，保留 IPC 计数用于有条件的观察。

fio 解析使用各 job 完成 IO 数加权的总平均时延；读写方向合并，带宽统一为 MiB/s。不会平均多个 p99 得出一个虚假 p99，`p99_ms` 当前为 null。顺序 stonewall jobs 不能按并发 IOPS 合并，会拒绝。此入口当前每次生成一个场景；测试调度器可在同一批次中组装多场景，后端按完整规范 fio 模型配对。

将生成的 JSON 导入“版本内排查”页面，或由现有工具调用 `POST /api/version/batches`。需要两份实测批次才有实测上次／本次对比；它们不能与 mock 示例互配。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s collectors/tests -v
```

测试只使用合成 proc/perf/fio 文件、模拟时间及内存数据，覆盖计数复用缩放、缺失数据、CPU guest 计数去重、NUMA 解析、安静线程发现、fio 单位与时延加权、平台限制、批次模型和窗口校验，并验证预热排除、绝对采样 deadline、动态 TID 复用、分窗真实完成数、CPU 迁移、SMT/IRQ 保留及分窗 PMU 的时间对齐。真实 Linux perf 运行、性能扰动和具体 HCI 版本兼容性需在环境接入后验证。
