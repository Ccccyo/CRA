from dataclasses import dataclass


@dataclass
class Flag:
    level: str  # "error"：几乎肯定错了；"warn"：需要人看一眼
    field: str
    message: str
