# GLEIF LEI 实体识别：公开资料核查卡（非内部政策）

> 类型：项目对 GLEIF 官方网页的中文摘要；核查日：2026-10-23；适用范围：有 LEI 的法人主体识别参考；状态：staging。不是客户实时 LEI 查询结果，也不定义本项目的自动匹配或授信规则。来源：[GLEIF LEI Data: Access & Use](https://www.gleif.org/en/lei-data/access-and-use-lei-data)、[GLEIF How to use LEI Search](https://www.gleif.org/en/lei-data/lei-search/about-lei-search/how-to-use-lei-search)。

## GLEIF-ID-01 · 对 CRA 企业识别的作用与范围

GLEIF 的 Level 1 数据回答“谁是谁”，包括法律实体的正式名称、注册地址等；Level 2 数据回答“谁拥有谁”，但关系可能存在报告例外。GLEIF 提供免费 LEI 搜索、下载文件和 API，可用于寻找和核对候选主体。它不能替代本项目 SAP BP 与客户申请主体的授权绑定，也不能仅凭名称相似就认定为同一法人。

## GLEIF-ID-02 · 可核查字段及边界

核查一条候选记录时，应保留 LEI、正式名称、注册地、注册地址、注册状态、最近更新/下次续期时间、查询时间、官方记录 URL 和与 SAP BP 的匹配证据。若名称、地域或注册编号冲突，保留多个候选并交人工确认；不要把关联公司自动并入申请主体。GLEIF 解释 Level 1 的 `LAPSED` 为 LEI 到续期时点尚未按期验证，**不等于实体已停止经营**；是否接受该状态是使用方的业务决定，项目尚无已批准规则。

## GLEIF-ID-03 · 更新与缺口

此卡只解释数据源能力，具体客户事实须在评审时从 GLEIF 官方接口/页面取得带时间戳的记录，并核查来源使用条款。查不到 LEI 不能推断企业不存在；应使用获授权的注册资料及人工核验。官方字段定义或状态解释变更时复核本卡，撤回旧摘要；不得把本卡当作客户识别结果引用。
