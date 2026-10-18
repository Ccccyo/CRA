"""让大模型复核候选排序并用大白话解释。

防幻觉：模型只能从给定候选里选；返回了不存在的编号会被丢弃。
"""
import json
from typing import List, Optional

from pydantic import BaseModel

from cra.llm import LLMClient, complete_json
from cra.lookup.matcher import LookupResult

SYSTEM = """你是信用分析助手，帮分析师从 D&B 候选公司中找出要查的客户。
规则：
1. 只能从给定候选中选择，用 entity_id 指代，不得编造公司或信息。
2. 依据名称、别名、地址、城市、邮编、州判断；说明相似点和不一致点。
3. 如果没有候选足够可信，no_good_match 设为 true。
只输出 JSON：{"ranking":[{"entity_id":"...","reason":"..."}],"no_good_match":false,"summary":"..."}"""


class RankedItem(BaseModel):
    entity_id: str
    reason: str


class LookupExplanation(BaseModel):
    ranking: List[RankedItem]
    no_good_match: bool
    summary: str = ""


def explain(result: LookupResult, query: dict, llm: LLMClient, model: Optional[str] = None,
            top_n: int = 5) -> LookupExplanation:
    cands = [
        {"entity_id": c.entity.entity_id, "name": c.entity.name, "trade_style": c.entity.trade_style,
         "address": c.entity.address, "city": c.entity.city, "state": c.entity.state,
         "postcode": c.entity.postcode, "rule_score": c.score}
        for c in result.candidates[:top_n]
    ]
    user = (f"查询：{json.dumps(query, ensure_ascii=False)}\n"
            f"规则提示：{'；'.join(result.warnings) or '无'}\n"
            f"候选：{json.dumps(cands, ensure_ascii=False)}")
    out = complete_json(llm, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                        LookupExplanation, model=model)
    valid_ids = {c["entity_id"] for c in cands}
    out.ranking = [r for r in out.ranking if r.entity_id in valid_ids]
    return out
