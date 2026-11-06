---
name: account-maintenance
description: Use when 用户要求日常记账、收取手机账单、导入本地账单、审核消费分类与四池、核对入账结果，或纠正已入账记录。
---

# 日常记账

适用于本人的 Account SQLite 账本，名称保留兼容。配置、安装和更新按项目入口所指文档处理，不在本 Skill；日常任务不重跑初始化或通读整套配置文档。使用目录的所有者文档可能保留旧版文字；版本契约有疑义时，以已核验更新状态 `releaseDocs` 中同版通用说明为准，仍保护本人明确确认。

## 先定位本批

1. 核对本人项目绝对 `ROOT`，使用项目托管 Python `PY`（Windows `.venv/Scripts/python.exe`，macOS `.venv/bin/python`）。读现有 `local/档案.md`、`local/个人规则.md` 及 `PROJECT_MEMORY.md` 的相关部分；Agent 旧判断不是本人批准的规则。缺个人文档不代表未初始化。
2. `PY scripts/account.py --root ROOT doctor` 和 `inspect` 核对来源、期间与身份。发布门禁另核对 `.runtime/ledger/publish.json` 的 `enabled` 和对应已安装 publisher 的 `--status-json` 中 `publishEnabled`，只取状态、不输出凭据；doctor/inspect 不能证明双门禁状态。`data/account.sqlite3` 是唯一当前账本；缺库/来源异常停止，不从表格、演示数据或他人目录回退。已有门禁可能使 import/refresh 自动发布，核对本次授权覆盖该行为，保留门禁及身份。
3. 先拿本批来源，再批量 prepare。已授权手机收件记账时，先 `inbox-fetch --apply` 收取，再 `inbox-status` 获取本批归档路径；fetch 返回计数，不直接提供归档路径。只读检查才单用 status，不能因尚无本机条目跳过收取去扫描旧文件。上传/收取不是入账。本地任务只将授权文件复制到 `ROOT/sra/`，核对复制前后 SHA-256；保留原件，同名不同内容不覆盖，目录不得是链接/junction。prepare 使用归档绝对路径。
4. 多文件一次 `prepare --source 文件1 文件2 --output ROOT/.runtime/本批审核.json`。先看候选、重复和冲突，不先遍历旧 `sra/`、旧审核文件或搜索历史审核者。确需补退款关联/旧确认时按具体 ID 有限查询，不扫无关历史。

## 实际审核，不逐笔盘问

先确定事件与本人净额，再主分类和四池。已授权“帮我记账”即连续完成本批审核、入账、核验；不用另问是否可 apply。纯检查保持只读。仅高影响、事件/净额/关联不明或真实规则冲突先问；可明确分离的未决候选留待处理，不拖住已确定部分。

先识别退款、报销、返款、代垫、账户转移和本金划转，再主分类和四池。退款与原支出有证据关联时按净额统计：不把退款记成新增收入，也不与原支出重复计消费。原支出可能在本批、旧批次或已入账历史：优先按平台加交易订单号/商家订单号定位，无订单号时按时间、金额、商户和说明组合核对，不凭同额自动关联；必要时按具体 ID 有限查询。跨月退款保留真实发生时间与关联证据；原交易已入账时按 [已入账纠正](references/review-contract.md#已入账纠正) 补关联或修正。代刷/返款、代垫/报销、账户间转移、余额宝/零钱通等本金划转分别识别，避免重复消费；借款不等于收入，投资收益不混入消费。交易拆分保留原交易，不重复加入原金额，分摊总额与原净额一致。

用途明确的日常支出据证据完成分类和分池，即使未设预算；不能因为商户通用、没有长期私人规则就把整批留 Unknown。不按商户、金额或主分类机械映射，不搬用作者或他人的私人商户经验。

| 资金池 | 用途依据 |
|---|---|
| 运行账户 | 维持日常生活和通勤 |
| 随心消费 | 自主娱乐、可选择的享受 |
| 旅行/体验 | 明确旅行或体验事件 |
| 成长/项目/耐用品 | 学习、明确项目或持续使用的耐用品 |

保留本人已确认的例外；退款继承已确认原交易池并核对关联及净额。少量小额、金额和事件已确定但用途真不明时，可保留实际金额与 Unknown/待归类，在 `语义说明`/`资金池判定` 写具体原因，入账后简短说明待确认事项；不为这些项目逐笔追问，也不为覆盖率猜完。金额未知绝不能填零，净额/事件不清的候选不得伪装成确定消费。

## 审核文件与执行

沿用 prepare 的完整 JSON，逐行填写六项：`最终财务事件`、`统计口径`、`计入消费金额`、`主分类`、`资金池`、`语义说明`。填写实际 `reviewed_by`、带时区 `reviewed_at`、理由和真实 `confirmation_source`；用当前实际 Agent 标识，不找旧审核者姓名。完整保留 `source_files`、路径/哈希、原始字段、ID 和生成事实。`review_required=false` 不替代审核。规范值、部分暂缓及更新格式见 [审核契约](references/review-contract.md)。

```text
PY scripts/account.py --root ROOT import --input ROOT/.runtime/本批审核.json
PY scripts/account.py --root ROOT import --input ROOT/.runtime/本批审核.json --apply
PY scripts/account.py --root ROOT verify
PY scripts/account.py --root ROOT import --input ROOT/.runtime/本批审核.json
```

预检检查净额、关联、冲突及 `classificationReview` 的覆盖、警告和 `reviewComplete`。有待分类/分池时该标志为 false，应如实报告，不能称全部分类完成；有理由的少量待定不阻止已授权入账。写前工具使用 Backup API，整批校验后事务提交。verify 后重复预检应无新增/更新，修订和 sourceHash 不再改变。快照由统一 Python 派生，不手改汇总。已提交但快照/回执失败按 [恢复与续接](references/recovery.md)处理，不重导成功记录。

## 用户纠正

“这几笔是共享单车”是本次明确确认；用只读 `find-records --month YYYY-MM --query 关键词 --limit 20` 有限定位现有 ID（完整命令见 [审核契约](references/review-contract.md#已入账纠正)），按已知用途检查分类/池，以 `updates` 修改派生字段，理由和出处引用该回复，然后预检、apply、verify、幂等核对。已有授权连续完成，不再索取第二次入账批准；不要重构原始行或重新导入。仅仍存在影响净额/用途的真实歧义才问。单笔确认不推广为长期商户规则，Agent 历史笔记不冒充批准。

## 汇报

报告本批期间/平台覆盖，新增、更新、重复、暂缓笔数及本人净消费；主分类和池分别报告已完成/待确认笔数、净额及原因。真实不计消费记录保留在账本，却不进入消费者快照的消费明细，说明口径，不能误报丢单。观察模式报告预算未制定，真实分池消费照常统计，不产生预算欠账或安全积累，不擅改额度。分开报告账本、verify/幂等、快照、云端回读、回执及设备结果，未执行不报通过。

不删除原件或清空 `.runtime`，不直接 SQL 改业务，不改共享源码解决个人分类。Android 不直接写账本；凭据、配对及完整订单不进聊天、日志或共享仓库。
