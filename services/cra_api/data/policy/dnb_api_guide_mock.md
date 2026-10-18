# D&B 接口使用说明（MOCK · 字段与错误码为演示用虚构内容）

## Lookup（查找 DUNS）
Lookup 使用 POST 请求，根据客户名称、国家、州、地址、城市、邮编或已知 DUNS 搜索候选公司。
已知 DUNS 时可以直接精确查询；不知道 DUNS 时，用 Customer Name + Country 组合查询。
国家为 US 或 CA 时必须填写 State，否则同名公司容易认错。
空值字段不要传入请求，否则接口可能校验失败。
返回结果包含 ACCOUNT_DUNS_NBR、ACCOUNT_CUST_NME、地址、CONF_CODE（置信度，1–10，越高越可信）和 MATCH_GRADE（匹配等级码）。
CONF_CODE 低于 6 时，应结合地址、邮编、城市人工确认，不能直接采用。

## Merge（绑定账户）
Merge 使用 PUT 请求，把确认后的 DUNS 绑定到 eRAM Account，并触发数据刷新。
必填字段：ACCOUNT_ACCT_NBR（账户号）和 REFRESH_CREDIT_LIMIT_FLAG（填 Y 表示刷新）。
新账户或更换 DUNS 时必须填写 DUNS；已经绑定过 DUNS、只需刷新数据时可以不填。

## Merge 返回 200 不等于成功
Merge 是异步处理的，返回 200 OK 只表示请求被接收，不代表数据已经入库。
需要通过 Lookup 按 DUNS 反查账户、查看 FS_ 开头的财务字段是否有值、或在 eRAM 页面确认刷新时间。
如果 FS_ 字段全部为空，先检查 Product Profile 是否包含财务数据，再判断是否绑定失败。

## Product Profile
Product Profile 决定 eRAM 能从 D&B 拉回哪些数据。
- 国家为 US 或 PR：使用 US Fin Stress and Score with Global Financials。
- 其他国家：使用 Global eRAM and Global Financials。

## 错误码
- EAPI030：Invalid Credentials，用户名、密码或 Database 参数错误。先用 Postman 验证凭证，再检查 Header params 表是否有多余空格。
- EAPI135：Content is null，请求体为空或 JSON 拼接错误。检查是否有字段值为空却仍被传入。
- EAPI210：Account Not Found，账户号不存在。确认账户号和国家组合是否正确。
- EAPI305：Invalid DUNS，DUNS 无效或不在当前 Product Profile 覆盖范围内。
