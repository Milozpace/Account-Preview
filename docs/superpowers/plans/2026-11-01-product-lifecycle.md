# Preview 开发、同步、发布与更新实施计划

> **For agentic workers:** 使用 subagent-driven-development，按任务独立实现并在集成后审查。用户已批准本方案，连续实施；不创建 Release，不改变仓库可见性。发布说明由用户撰写。

**Goal:** Preview 成为共享源码来源；现有个人 Account 和新用户由 Agent 更新已发布版本，保护各自数据、规则、凭据和应用身份。

**Architecture:** 一个源码仓库，preview/personal 两个安装身份；版本清单统一生成原生版本。发布工具仅准备候选和校验；更新工具选已发布试用版本，先预检和备份，校验源码/组件后安装。过渡同步工具以明确文件清单和内容基线做三方判断，冲突不覆盖。

**Tech Stack:** Python 标准库/unittest、现有 bootstrap、Git/GitHub CLI、Tauri/现有 CI。

## 1. 版本与发布准备

- [x] 新增版本模块及 tests/test_product_version.py，先测试无效/降级/版本不同步，再实现 release.json 为唯一版本输入；候选 0.4.1-preview.1，原生 0.4.1，Android buildCode 严格增加。
- [x] 实现 preview/personal 构建配置，保留 com.account.preview/AccountPreview 及既有 com.account.probe/AccountDesktop，各平台安全存储不迁移。
- [x] release.py 新增版本检查、发布清单准备；清单绑定源码提交、来源/身份/平台/哈希/兼容性。用户提供 notes 文件；工具不代写、不调用发布 API、不移动标签。
- [x] CI 去掉固定版本字符串，构建 personal Windows/Android 候选及 preview 四平台；PR/push 只产 artifacts。
- [x] 用项目 Python 运行新增测试及已有 release/bootstrap 测试，提交相关文件。

## 2. 更新入口

- [x] 新增 scripts/product_update.py 与 tests/test_product_update.py，失败测试覆盖仅选择已发布的 preview、拒绝源码或安装身份混用、损坏哈希、路径穿越、冲突保留、重复更新和安装失败恢复。
- [x] CLI status/adopt/check/apply；默认预检，显式 --apply 写入；支持本机已认证 gh，公开仓库允许匿名读取；支持离线 release 目录作可复现验收。
- [x] 个人 Account 显式采用 personal 档案，不安装 preview 包。新安装默认 preview；未知既有项目不猜身份。更新只修改受管理程序；私人规则、原件、库、来源配置、身份及发布门禁保持。
- [x] SQLite Backup API 备份；版本化暂存；检查本地程序与基线冲突；先完整校验，再原子逐文件替换并有恢复日志；失败回滚程序，不回滚业务。
- [x] 架构/数据兼容性不满足停止，不自动迁移账本，不调用 refresh/publish/init。升级前后比较账本业务哈希与个人保护文件；安装和二进制外部失败如实报告。
- [x] 项目 Python 执行相关测试，提交相关文件。

## 3. 过渡同步与月份修复

- [x] 新增 scripts/source_sync.py 和 tests/test_source_sync.py；只接受受控清单的显式源码文件，保存 source/target 基线，重复不变，双方修改报冲突，拒绝数据/身份/路径跳转；默认检查。
- [x] 迁入个人版 1088208、App 工作树 9ac3627 的通用历史月份/焦点修复；按语义合并，不覆盖 Preview 的 Mac/引导/身份适配。
- [x] tests/test_month_history.py 等合成回归验证；更新入门说明中历史月份范围，使用个人账本起点而非强制每人从作者九月开始。

## 4. 规范、入口与验收

- [x] README、AGENTS、开发指南、发布检查、更新与恢复，新增开发同步发布规范及用户手写发布说明模板。新代码在 Preview；原个人项目只消费版本；发布必须用户本轮确认+本人 notes 文件。
- [x] 在原 Account 增加更新说明/入口和开发路由，保留所有既有未提交内容；本轮不升级已安装 App、不改账、不发布云快照。
- [x] 以临时两用户根和虚构 release 目录验收源码更新、重复、冲突、失败、保护文件；运行 Python/前端/原生适当检查及源码隐私扫描。
- [x] 提交并推送已授权源码到私有仓库；公开化仅准备检查说明，仍保持私有；不发新版，列用户下一步填写的版本说明入口。
