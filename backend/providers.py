"""Reasoning-provider boundary. No model credentials are required for the demo."""

import json
import os
from typing import Protocol

import httpx


class ReasoningProvider(Protocol):
    name: str

    def explain(self, case: dict, evidence: list[dict]) -> str: ...


class DemoProvider:
    name = "deterministic_demo"

    def explain(self, case, evidence):
        return "演示推理：模拟数据中的 tierd 调度延迟升高，而设备忙碌度未饱和；先将线程竞争列为候选，再用单变量 A/B 实验验证。该结论不能用于判断真实集群。"


class CompatibleChatProvider:
    """Optional HTTP model adapter, explicitly configured by the operator.

    This only asks for a narrative assessment. Tool selection and experiment
    approval stay in the typed graph; model text never executes commands.
    """

    name = "chat_completions"

    def __init__(self):
        self.url = os.environ["HCI_LLM_BASE_URL"].rstrip("/") + "/chat/completions"
        self.model = os.environ["HCI_LLM_MODEL"]
        self.api_key = os.environ.get("HCI_LLM_API_KEY", "")

    def explain(self, case, evidence):
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {"model": self.model, "messages": [
            {"role": "system", "content": "你是存储性能分析助手。仅根据提供的证据给出候选解释及待验证项。数据为 mock 时必须明确说明，不能声称已确认真实根因。IOR 前为协议层；共用业务从 IOR 开始。仅考虑本地与一个远端的数据布局。"},
            {"role": "user", "content": json.dumps({"case": {key: case[key] for key in ("title", "workflow_id", "protocol", "scenario")}, "evidence": evidence}, ensure_ascii=False)},
        ]}
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.post(self.url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()["choices"][0]["message"]["content"]
        if not isinstance(result, str) or not result.strip():
            raise ValueError("Configured reasoning provider returned empty content")
        return result[:10000]


def configured_provider():
    name = os.environ.get("HCI_LLM_PROVIDER", "demo")
    if name == "demo":
        return DemoProvider()
    if name == "chat_completions":
        return CompatibleChatProvider()
    raise ValueError(f"Unknown HCI_LLM_PROVIDER: {name}")
