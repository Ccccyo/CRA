"""公司名清洗：统一大小写和符号、去掉法律后缀、展开常见缩写、中文转拼音。

目的是让 "ABC Trading Co., Ltd." 和 "abc trading" 在比较时被当成同一个名字。
"""
import re
import unicodedata

# 法律形式后缀：比较名字时没有区分意义
LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "plc", "gmbh", "ag", "sa", "sas", "sarl", "srl", "spa", "bv", "nv", "kk", "pte", "pty",
    "lp", "llp", "ab", "oy", "as", "sl", "de", "cv",
}
CN_SUFFIXES = ["股份有限公司", "有限责任公司", "有限公司", "集团", "公司"]

ABBREVIATIONS = {
    "intl": "international", "int'l": "international", "mfg": "manufacturing",
    "chem": "chemical", "chems": "chemicals", "ind": "industries", "inds": "industries",
    "tech": "technology", "hldgs": "holdings", "hldg": "holding", "svcs": "services",
    "&": "and",
}


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


def to_pinyin(text: str) -> str:
    from pypinyin import lazy_pinyin

    return " ".join(lazy_pinyin(text))


def normalize_name(name: str) -> str:
    text = name.strip()
    if has_cjk(text):
        for suffix in CN_SUFFIXES:
            text = text.replace(suffix, "")
        text = to_pinyin(text)
    text = _strip_accents(text).lower().replace("&", " & ")
    tokens = re.findall(r"[a-z0-9&']+", text)
    tokens = [ABBREVIATIONS.get(t, t) for t in tokens]
    tokens = [t for t in tokens if t not in LEGAL_SUFFIXES]
    return " ".join(tokens)
