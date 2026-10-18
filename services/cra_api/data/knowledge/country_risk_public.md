# 国家风险（公开资料：OECD 国家风险分类）

> 来源：OECD《Country Risk Classifications of the Participants to the Arrangement on Officially Supported Export Credits》，
> 生效日期 26 June 2026，共 201 个国家/地区。原文：https://www.oecd.org/content/dam/oecd/en/topics/policy-sub-issues/country-risk-classification/cre-crc-current-english.pdf
> 层级：公开资料，只作参考；与公司信用政策冲突时以公司制度为准。

## OECD 分类是什么
- OECD 出口信贷安排的参与方用这个分类衡量「国家风险」：转移与兑换风险（钱汇不出来、换不了汇）和不可抗力（战争、征收、自然灾害等）。
- 分为 0–7 共 8 类，**0 风险最低，7 风险最高**。
- 高收入 OECD 国家和高收入欧元区国家**不评审、不分类**（表中为「-」），按惯例视为最低风险。
- 分类每年多次更新（通常一年三次会议后调整），引用时要写明生效日期。
- 它衡量的是国家层面的风险，不是某家企业的信用；也不等同于标普、穆迪的主权评级。

## 在 CRA 定性评分里怎么用（演示映射，属于假设）
CRA 模板的 Sovereign Rating 只有三档。演示中按下面规则从 OECD 类别给出参考档位，最终由分析师按公司制度选择：
- 不分类的高收入国家、类别 0–2 → grade A or above
- 类别 3–4 → grade B
- 类别 5–7、或未评审 → grade C or Not rated

## 演示涉及国家的当前分类
| 代码 | 国家 | OECD 类别 | 说明 | 参考档位（演示映射） |
|---|---|---|---|---|
| AR / ARG | 阿根廷 Argentina | 7 | — | grade C or Not rated |
| BR / BRA | 巴西 Brazil | 4 | — | grade B |
| CA / CAN | 加拿大 Canada | - | 高收入 OECD 国家，不分类（按惯例视为最低风险） | grade A or above |
| CL / CHL | 智利 Chile | - | 高收入 OECD 国家，不分类（按惯例视为最低风险） | grade A or above |
| CN / CHN | 中国 China (People’s Republic of) | 2 | — | grade A or above |
| FR / FRA | 法国 France | - | 高收入 OECD 国家，不分类（按惯例视为最低风险）；高收入欧元区国家，不分类 | grade A or above |
| DE / DEU | 德国 Germany | - | 高收入 OECD 国家，不分类（按惯例视为最低风险）；高收入欧元区国家，不分类 | grade A or above |
| IN / IND | 印度 India | 3 | — | grade B |
| JP / JPN | 日本 Japan | - | 高收入 OECD 国家，不分类（按惯例视为最低风险） | grade A or above |
| KR / KOR | 韩国 Korea | - | 高收入 OECD 国家，不分类（按惯例视为最低风险） | grade A or above |
| MX / MEX | 墨西哥 Mexico | 3 | — | grade B |
| TR / TUR | 土耳其 Türkiye | 5 | — | grade C or Not rated |
| GB / GBR | 英国 United Kingdom | - | 高收入 OECD 国家，不分类（按惯例视为最低风险） | grade A or above |
| US / USA | 美国 United States | - | 高收入 OECD 国家，不分类（按惯例视为最低风险） | grade A or above |
| VN / VNM | 越南 Viet Nam | 3 | — | grade B |

## 本期调整的国家
- Namibia（NAM）：6 → 5
- Viet Nam（VNM）：4 → 3

## 属地怎么查
属地先按「国家代码替换表」替换再查，例如 YT（马约特）→ FR，PR（波多黎各）→ US。完整 201 个国家见 country_risk_oecd.csv。

## Coface 国家评估（补充参考）
- Coface 按 A1、A2、A3、A4、B、C、D、E 共 8 档评估一国企业的平均违约风险，A1 最低、E 最高；另有「营商环境」评估。
- 与 OECD 的区别：OECD 看国家转移风险，Coface 更贴近当地企业付款表现。两者结论不一致时，在意见里同时写明。
- 本知识库未收录 Coface 逐国数值（需在 Coface 官网查询最新评估并注明日期）。
