"""Two unbound example parameters and twelve explicitly synthetic trials."""


def example_parameters():
    return [
        {"id": "demo.vhost_coalescing", "label": "vhost coalescing（示例参数）", "component": "vhost",
         "value_type": "integer", "unit": "演示档位", "protocols": ["vhost"],
         "description": "仅演示参数配置对比；实际产品参数名称、单位、范围及执行接口待提供，不能直接应用到真实设备。",
         "observables": ["iops", "latency_ms", "p99_ms"], "maturity": "example", "adapter_key": None},
        {"id": "demo.bdev_depth", "label": "bdev 深度（示例参数）", "component": "bdev",
         "value_type": "integer", "unit": None, "protocols": ["nfs", "vhost", "iscsi"],
         "description": "演示下发深度与 tierd 深度、盘时延及吞吐的观测关联；实际产品参数名称、单位、范围、采集及执行接口待提供，不能直接绑定真实配置。",
         "observables": ["iops", "disk_latency_ms", "tierd_depth"], "maturity": "example", "adapter_key": None},
    ]


def demo_campaign():
    return {"id": "POC-DEMO-VHOST", "title": "外部 POC · vhost 参数调优（模拟）", "source": "mock",
            "environment": {"id": "poc-tuning-demo-fixed-01", "label": "客户设备 A（模拟）",
                            "hardware": {"data_hosts": 2, "replica_layout": "local+one_remote", "device_profile": "仅示例设备档案；非已连接环境"}},
            "build": "demo-build-fixed", "protocol": "vhost",
            "fio": {"rw": "randwrite", "bs": "4k", "iodepth": 64, "numjobs": 8, "vm_count": 1, "ioengine": "libaio", "direct": 1},
            "goal": {"metric": "iops", "p99_limit_ms": 1.8, "min_repeats": 3},
            "baseline_parameters": {"demo.vhost_coalescing": 2, "demo.bdev_depth": 64}}


def demo_trials(campaign):
    configurations = [("baseline", 2, 64, 100000, .75, 1.3, .35, 46),
                      ("coalescing", 4, 64, 118000, .68, 1.25, .34, 45),
                      ("bdev-small", 2, 32, 80000, .62, 1.1, .23, 26),
                      ("bdev-large", 2, 128, 72000, 1.1, 2.2, .68, 91)]
    rows = []
    for name, coalescing, depth, iops, latency, p99, disk, tierd in configurations:
        for number, factor in enumerate((.985, 1, 1.02), 1):
            rows.append({"id": f"POC-DEMO-{name}-{number}", "environment_id": campaign["environment"]["id"],
                         "build": campaign["build"], "protocol": campaign["protocol"], "fio": campaign["fio"], "source": "mock",
                         "parameters": {"demo.vhost_coalescing": coalescing, "demo.bdev_depth": depth},
                         "metrics": {"iops": round(iops * factor), "bandwidth_mib_s": round(iops * factor * 4096 / 1048576, 3),
                                     "latency_ms": round(latency / factor, 4), "p99_ms": round(p99 / factor, 4),
                                     "disk_latency_ms": round(disk / factor, 4), "tierd_depth": round(tierd * factor, 3)},
                         "note": "MOCK：参数与所有测试指标为模拟；没有修改设备或运行真实 fio。",
                         "artifacts": [{"kind": "demo", "path": f"mock://poc/{name}/{number}", "note": "仅模拟引用，无真实采集文件。"}]})
    return rows
