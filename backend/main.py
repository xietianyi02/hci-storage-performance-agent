"""Local demo API and optional built frontend. Run with a single worker."""

from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .graph import AgentService
from .models import ApprovalInput, CreateCase, FeedbackInput
from .version_models import Batch, VersionFeedback
from .version_service import VersionService
from .poc_models import ParameterDefinition, PocCampaignInput, PocFeedback, PocTrialInput
from .poc_service import PocService


ROOT = Path(__file__).resolve().parents[1]


def knowledge(service):
    entries = [
        {"id": "architecture", "title": "IOR：协议层与业务层的边界", "category": "产品架构", "summary": "NFS、vhost、iSCSI 的差异在 IOR 之前；IOR 是共用业务入口。", "content": "三节点 HCI 集群为虚拟机提供分布式存储。性能入口统一为虚拟机内 fio。IOR 之前属于协议层，从 IOR 开始共用业务与存储链路。", "source": "user", "status": "confirmed", "tags": ["IOR", "分层", "三种协议"]},
        {"id": "scope", "title": "POC 聚合副本分析边界", "category": "产品架构", "summary": "仅分析本地与一个远端的数据布局。", "content": "当前项目只考虑 POC 聚合副本场景：数据保存到本地主机和一个远端主机。集群仍由三节点组成；不延伸分析非聚合副本布局。", "source": "user", "status": "confirmed", "tags": ["POC", "聚合副本", "本地 + 远端"]},
        {"id": "nfs_path", "title": "NFS IO 请求与完成链路", "category": "产品架构", "summary": "沿请求下发与完成返回分别建立证据。", "content": "根据用户提供的 IO 流文档：NFS 前端经过 guest 块层、virtio-blk、QEMU、QCOW2、libnfs、同机 Unix socket 到 asan-stord；业务从 IOR 开始，经 snapshot/shard、route/AFR、DATA glusterfsd、tier、tierd-core、tierd-aio、io_uring、DM/NVMe。写包含本地与远端数据路径。文档 PID、TID、IP 是采样实例；vhost、iSCSI 前端详情待补充。", "source": "user", "status": "confirmed", "tags": ["NFS", "请求", "完成", "IO 链路"]},
        {"id": "method", "title": "单变量验证与证据判定", "category": "定位方法", "summary": "建议有效性由实验与专家评价共同决定。", "content": "固定 fio 模型、版本和副本布局，每次只改变一个因素。记录实验前后 IOPS、带宽、时延、样本波动及回滚方式。候选假设、相关性和已验证结果应分开记录。正确建议占比 = 已确认 /（已确认 + 已否决），待验证建议不计入分母。模拟案例只衡量演示流程。", "source": "method", "status": "confirmed", "tags": ["A/B", "可追溯", "专家评价"]},
    ]
    for entry in entries:
        entry["updated_at"] = "2026-09-29T00:00:00+00:00"
    for case in service.store.all_runs():
        for recommendation in case["recommendations"]:
            if recommendation["verdict"] == "confirmed":
                entries.append({"id": f"{case['id']}:{case['run_id']}:{recommendation['id']}", "case_id": case["id"], "title": recommendation["title"], "category": "案例复用", "summary": recommendation["feedback_note"], "content": recommendation["reason"] + "\n" + recommendation["action"] + "\n仅演示知识候选，真实环境尚未验证。", "source": "mock", "status": "candidate", "tags": [case["workflow_title"], case["protocol"].upper(), "模拟案例"], "updated_at": case["updated_at"]})
    return entries


def create_app(data_dir: Path | None = None, *, seed=True, service_factory=AgentService):
    @asynccontextmanager
    async def lifespan(application):
        directory = data_dir or Path(os.environ.get("HCI_DATA_DIR", ROOT / "work" / "data"))
        service = service_factory(directory, seed=seed)
        application.state.service = service
        application.state.version_service = VersionService(directory, seed=seed)
        application.state.poc_service = PocService(directory, seed=seed)
        try:
            yield
        finally:
            application.state.poc_service.close()
            application.state.version_service.close()
            service.close()

    application = FastAPI(title="HCI 超融合存储性能专家 Agent", version="0.1.0", lifespan=lifespan)
    application.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    def service(request: Request):
        return request.app.state.service

    def invoke(operation, *args):
        try:
            return operation(*args)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="案例或建议不存在。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/health")
    def health(request: Request):
        current = service(request)
        return {"status": "ok", "mode": "demo", "data_source": "mock", "framework": "LangGraph", "persistence": "SQLite", "reasoning_provider": current.provider.name, "lab_connected": False,
                "process_id": os.getpid(), "parent_process_id": os.getppid()}

    @application.get("/api/overview")
    def overview(request: Request):
        return service(request).overview()

    @application.get("/api/cases")
    def list_cases(request: Request):
        return service(request).store.list()

    @application.post("/api/cases", status_code=201)
    def create_case(payload: CreateCase, request: Request):
        return invoke(service(request).create_case, payload)

    @application.get("/api/cases/{case_id}")
    def detail(case_id: str, request: Request):
        return invoke(service(request).store.get, case_id)

    @application.post("/api/cases/{case_id}/run")
    def run(case_id: str, request: Request):
        return invoke(service(request).run, case_id)

    @application.get("/api/cases/{case_id}/runs")
    def historical_runs(case_id: str, request: Request):
        runs = invoke(service(request).store.list_runs, case_id)
        return [{"id": item["run_id"], "status": item["status"], "started_at": item["runs"][-1]["started_at"], "finished_at": item["runs"][-1].get("finished_at"), "title": item["title"], "workflow_id": item["workflow_id"], "metrics": item["metrics"], "source": item["data_source"], "recommendations": {"total": len(item["recommendations"]), **{verdict: sum(rec["verdict"] == verdict for rec in item["recommendations"]) for verdict in ("confirmed", "rejected", "pending")}}} for item in runs]

    @application.get("/api/cases/{case_id}/runs/{run_id}")
    def historical_run(case_id: str, run_id: str, request: Request):
        return invoke(service(request).store.get_run, case_id, run_id)

    @application.post("/api/cases/{case_id}/approve")
    def approve(case_id: str, payload: ApprovalInput, request: Request):
        return invoke(service(request).approve, case_id, payload.model_dump())

    @application.post("/api/cases/{case_id}/feedback")
    def feedback(case_id: str, payload: FeedbackInput, request: Request):
        return invoke(service(request).feedback, case_id, payload)

    @application.get("/api/workflows")
    def workflows(request: Request):
        return service(request).workflows.list()

    @application.get("/api/tools")
    def tools(request: Request):
        return service(request).tools.list()

    @application.get("/api/knowledge")
    def list_knowledge(request: Request):
        return knowledge(service(request))

    def version_service(request: Request):
        return request.app.state.version_service

    def poc_service(request: Request):
        return request.app.state.poc_service

    def invoke_poc(operation, *args):
        try:
            return operation(*args)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="POC 任务、参数或分析记录不存在。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/api/poc/parameters")
    def poc_parameters(request: Request):
        return poc_service(request).parameters()

    @application.post("/api/poc/parameters", status_code=201)
    def poc_register_parameter(payload: ParameterDefinition, request: Request):
        return invoke_poc(poc_service(request).register_parameter, payload)

    @application.get("/api/poc/campaigns")
    def poc_campaigns(request: Request):
        return poc_service(request).campaigns()

    @application.post("/api/poc/campaigns", status_code=201)
    def poc_create_campaign(payload: PocCampaignInput, request: Request):
        return invoke_poc(poc_service(request).create_campaign, payload)

    @application.get("/api/poc/campaigns/{campaign_id}")
    def poc_campaign_detail(campaign_id: str, request: Request):
        return invoke_poc(poc_service(request).detail, campaign_id)

    @application.post("/api/poc/campaigns/{campaign_id}/trials", status_code=201)
    def poc_import_trial(campaign_id: str, payload: PocTrialInput, request: Request):
        return invoke_poc(poc_service(request).add_trial, campaign_id, payload)

    @application.post("/api/poc/campaigns/{campaign_id}/analyze")
    def poc_analyze(campaign_id: str, request: Request):
        return invoke_poc(poc_service(request).analyze, campaign_id)

    @application.post("/api/poc/campaigns/{campaign_id}/feedback")
    def poc_feedback(campaign_id: str, payload: PocFeedback, request: Request):
        return invoke_poc(poc_service(request).feedback, campaign_id, payload)

    @application.get("/api/version/batches")
    def version_batches(request: Request):
        return JSONResponse(version_service(request).batches())

    @application.get("/api/version/catalog")
    def version_catalog(request: Request):
        return JSONResponse(version_service(request).catalog())

    @application.get("/api/version/batches/{batch_id}/scenarios")
    def version_scenarios(batch_id: str, request: Request):
        return JSONResponse(invoke(version_service(request).scenario_summaries, batch_id))

    @application.post("/api/version/batches", status_code=201)
    def version_import(payload: Batch, request: Request):
        return invoke(version_service(request).import_batch, payload)

    @application.get("/api/version/import-example")
    def version_example(request: Request):
        return version_service(request).import_example()

    @application.get("/api/version/comparisons/{batch_id}/{scenario_id}")
    def version_comparison(batch_id: str, scenario_id: str, request: Request, include_samples: bool = True):
        return JSONResponse(invoke(version_service(request).comparison, batch_id, scenario_id, include_samples))

    @application.post("/api/version/comparisons/{batch_id}/{scenario_id}/analyze")
    def version_analyze(batch_id: str, scenario_id: str, request: Request, include_samples: bool = True):
        return JSONResponse(invoke(version_service(request).analyze, batch_id, scenario_id, include_samples))

    @application.post("/api/version/comparisons/{batch_id}/{scenario_id}/feedback")
    def version_feedback(batch_id: str, scenario_id: str, payload: VersionFeedback, request: Request, include_samples: bool = True):
        return JSONResponse(invoke(version_service(request).feedback, batch_id, scenario_id, payload, include_samples))

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @application.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Unknown API endpoint")
        if not (dist / "index.html").is_file():
            raise HTTPException(status_code=404, detail="Frontend not built. Run npm run build in frontend/.")
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    return application


app = create_app()
