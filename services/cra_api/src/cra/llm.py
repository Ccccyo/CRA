"""大模型统一接口。业务代码只依赖 LLMClient，换模型只改这里和 .env。

- DeepSeekClient：真实调用（OpenAI 兼容格式）。
- FakeLLM：按预设回复返回，用于单元测试和离线演示。
- complete_json：要求 JSON 输出，用 Pydantic 校验，不合格自动重试。
"""
import json
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError

from cra.config import get_settings
from cra.trace import log_event

Message = Dict[str, str]
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class LLMClient(Protocol):
    def complete(
        self,
        messages: List[Message],
        *,
        model: Optional[str] = None,
        json_mode: bool = False,
    ) -> LLMResult: ...


class DeepSeekClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        settings = get_settings()
        api_key = api_key or settings.deepseek_api_key
        if not api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填写。")
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url or settings.deepseek_base_url)
        self.default_model = settings.chat_model

    def complete(self, messages, *, model=None, json_mode=False) -> LLMResult:
        model = model or self.default_model
        kwargs = {"model": model, "messages": messages}
        if "reasoner" in model:
            pass  # 思考模型：部分版本不支持 JSON 模式和 temperature，靠提示词 + complete_json 校验重试
        else:
            kwargs["temperature"] = 0.0  # 抽取和问答要稳定，不要随机性
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

        start = time.time()
        resp = self._client.chat.completions.create(**kwargs)
        usage = resp.usage
        result = LLMResult(
            text=resp.choices[0].message.content or "",
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            latency_s=round(time.time() - start, 2),
        )
        log_event(
            "llm_call",
            {
                "model": model,
                "json_mode": json_mode,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_s": result.latency_s,
                "messages": messages,
                "output": result.text,
            },
        )
        return result


class FakeLLM:
    """测试用：responder 可以是固定字符串、字符串列表（依次返回）或函数。"""

    def __init__(self, responder: Union[str, List[str], Callable[[List[Message]], str]]):
        self._responder = responder
        self.calls: List[List[Message]] = []

    def complete(self, messages, *, model=None, json_mode=False) -> LLMResult:
        self.calls.append(messages)
        if callable(self._responder):
            text = self._responder(messages)
        elif isinstance(self._responder, list):
            text = self._responder.pop(0)
        else:
            text = self._responder
        return LLMResult(text=text, model=model or "fake")


def complete_json(
    client: LLMClient,
    messages: List[Message],
    schema: Type[T],
    *,
    model: Optional[str] = None,
    retries: int = 2,
) -> T:
    """要求模型输出符合 schema 的 JSON；解析或校验失败时把错误告诉模型并重试。"""
    history = list(messages)
    last_error = ""
    for _ in range(retries + 1):
        result = client.complete(history, model=model, json_mode=True)
        text = result.text
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:  # 去掉 ```json 代码块等包裹
            text = text[start:end + 1]
        try:
            return schema.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)[:800]
            history = history + [
                {"role": "assistant", "content": result.text},
                {"role": "user", "content": f"上面的 JSON 不合格：{last_error}\n请只输出修正后的 JSON。"},
            ]
    raise ValueError(f"模型输出 {retries + 1} 次都不符合格式：{last_error}")


def get_llm() -> LLMClient:
    return DeepSeekClient()
