"""认公司（模拟 D&B Lookup）：国家代码替换 → 按国家过滤 → 名称 + 地址打分 → 候选排序。

这一层完全是规则和打分，不用大模型；大模型只在 explain.py 里负责解释和复核排序。
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz

from cra.lookup.normalize import normalize_name

CONFIDENT_SCORE = 85.0  # 高于这个分数才算「可信匹配」
# 最高分低于这个，判定为「没有可信的匹配」。
# R1：70 → 80。BC-0001 中只有首词相同的公司（Corvel Printing vs Corvel Productions）得 72.7 被硬选；
# 当时所有正确匹配都 ≥ 96.5，提高到 80 不影响它们。80–85 之间提示人工确认。
NO_MATCH_SCORE = 80.0
STATE_REQUIRED = {"US", "CA"}


@dataclass
class Entity:
    entity_id: str  # 模拟 DUNS
    name: str
    country: str
    state: str = ""
    city: str = ""
    address: str = ""
    postcode: str = ""
    trade_style: str = ""
    paydex: str = ""
    failure_score: str = ""
    rating: str = ""


@dataclass
class Candidate:
    entity: Entity
    score: float
    name_score: float
    address_score: Optional[float]
    reasons: List[str] = field(default_factory=list)


@dataclass
class LookupResult:
    query_country: str
    effective_country: str
    effective_state: str
    candidates: List[Candidate]
    warnings: List[str] = field(default_factory=list)

    @property
    def best(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None

    @property
    def no_good_match(self) -> bool:
        return self.best is None or self.best.score < NO_MATCH_SCORE


def load_entities(path: Path) -> List[Entity]:
    with path.open(encoding="utf-8") as f:
        return [Entity(**{k: (v or "").strip() for k, v in row.items()}) for row in csv.DictReader(f)]


def load_country_mapping(path: Path) -> Dict[str, str]:
    """ISO 代码 → D&B 可接受的替换代码（如 YT → FR，PR → US）。"""
    with path.open(encoding="utf-8") as f:
        return {row["iso_alpha2"].strip().upper(): row["substitute_alpha2"].strip().upper()
                for row in csv.DictReader(f)}


def _name_score(query: str, entity: Entity) -> float:
    best = 0.0
    for candidate in filter(None, [entity.name, entity.trade_style]):
        a, b = normalize_name(query), normalize_name(candidate)
        best = max(best, fuzz.token_sort_ratio(a, b), fuzz.token_set_ratio(a, b) * 0.95,
                   fuzz.partial_ratio(a, b) * 0.9)
    return best


def _address_score(entity: Entity, city: str, address: str, postcode: str) -> Optional[float]:
    parts = []
    if city:
        parts.append(100.0 if normalize_name(city) == normalize_name(entity.city) else
                     fuzz.ratio(city.lower(), entity.city.lower()))
    if postcode:
        parts.append(100.0 if postcode.replace(" ", "") == entity.postcode.replace(" ", "") else 0.0)
    if address:
        parts.append(fuzz.token_set_ratio(address.lower(), entity.address.lower()))
    return sum(parts) / len(parts) if parts else None


class EntityMatcher:
    def __init__(self, entities: List[Entity], country_mapping: Dict[str, str]):
        self.entities = entities
        self.mapping = country_mapping

    def resolve_country(self, country: str, state: str = "") -> Tuple[str, str]:
        """属地代码替换。美国属地（PR/UM/VI）替换为 US 时，原代码作为州。"""
        country = country.strip().upper()
        target = self.mapping.get(country, country)
        if target == "US" and country != "US" and not state:
            state = country
        return target, state.strip().upper()

    def search(self, name: str, country: str, state: str = "", city: str = "",
               address: str = "", postcode: str = "", k: int = 10) -> LookupResult:
        eff_country, eff_state = self.resolve_country(country, state)
        warnings = []
        if eff_country != country.strip().upper():
            warnings.append(f"国家代码 {country.upper()} 已按映射表替换为 {eff_country}")
        if eff_country in STATE_REQUIRED and not eff_state:
            warnings.append(f"{eff_country} 必须填写州/省，否则同名公司容易认错")

        pool = [e for e in self.entities if e.country.upper() == eff_country]
        candidates = []
        for e in pool:
            name_s = _name_score(name, e)
            addr_s = _address_score(e, city, address, postcode)
            score = name_s if addr_s is None else 0.7 * name_s + 0.3 * addr_s
            reasons = [f"名称相似度 {name_s:.0f}"]
            if addr_s is not None:
                reasons.append(f"地址相似度 {addr_s:.0f}")
            if eff_state and e.state and e.state.upper() != eff_state:
                score -= 15
                reasons.append(f"州不一致（{e.state} ≠ {eff_state}），扣分")
            candidates.append(Candidate(e, round(score, 1), name_s, addr_s, reasons))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return LookupResult(country.upper(), eff_country, eff_state, candidates[:k], warnings)
