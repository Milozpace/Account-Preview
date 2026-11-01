---
name: account-maintenance
description: Use when a local Agent is asked to inspect, initialize, import, classify, verify, or recover this Account Preview SQLite ledger.
---

# Account 维护（可选）

此文件仅为仓库路由，不要求安装 Skill。先读 [AGENTS.md](../../../AGENTS.md)、[开始使用](../../../docs/开始使用.md)、[工作说明](../../../docs/工作说明.md)；已初始化再读本机个人档案、规则和工作记忆。没有本机文件时走初始化，不复制他人资料。

记账读[记账规则](../../../docs/记账规则.md)、[规则与学习](../../../docs/规则与学习.md)、[数据字典](../../../docs/数据字典.md)。在本项目绝对根目录用托管 Python：`doctor → inspect → prepare → Agent 审核 → import 预检 → import --apply → verify`。写入须已有授权，写前备份，整批事务，保留原件、ID、来源、关联和人工确认审计。

先事件与净额，再按记账规则的通用用途定义完成主分类和分池；这不依赖预算是否设定，不能因额度为零就把整批消费留待归类。用途明确的据证据分配，真实不明的才解释后保留 Unknown；不按商户、金额或主分类机械映射。预检核对 classificationReview（仅本批新增/修改记录）的覆盖与警告，分别报告已分配和待确认范围，不把待审定说成已完成。单笔确认只改该笔，高影响/反复歧义澄清，低影响可合并询问。长期规则须明确批准条件、例外、出处，不能凭金额推断或覆盖旧确认。

个人参数仅本机，新 init 可省略 amounts 默认零预算观察；首次正数额度前（未提供非零初始滚存）只统计实际分池消费，不产生预算欠账或安全积累。旧库不猜零值意图、不自动改模式。额度可在桌面“四池预算”或 budget-set 预检、确认、备份并审计，常规 budget 仍只追加；不改共享源文件或直接 SQL。凭据只进 App 原生界面。账本、快照、云端、设备、回执分报，失败保留快照；已授权开发验证后主动提交相关代码，不另询问 Git 提交许可。安装、发布、配对与远端操作按任务实际授权。恢复见[更新与恢复](../../../docs/更新与恢复.md)，不直接覆盖当前库。
