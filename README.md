# Account Preview

本地 Agent 协助记账，Windows / macOS 电脑维护唯一 SQLite 账本，Android 查看加密快照并投递原始账单。可使用任何能读仓库、执行本地命令的 Agent；本项目不提供 Agent、不要求安装某种 Skill，也没有模型 API 配置项。

独立私有预览版 `0.4.0-preview.1`。仓库不包含个人历史账本、预算、凭据或配对身份；每位使用者建立自己的账本与规则。平台与真实设备/云端验收见[交付计划](docs/交付计划.md)。

**第一次使用只有一个入口：[开始使用](docs/开始使用.md)。**

[下载 0.4.0-preview.1 私有试用包](https://github.com/Milozpace/Account-Preview/releases/tag/v0.4.0-preview.1)：电脑按 Windows x64、Mac Apple Silicon 或 Mac Intel 选择 ZIP，手机使用 Android arm64 APK。Agent 会校验并配置电脑包；手机安装见开始使用的第 4 步。访问下载页同样需要私有仓库权限。

准备私有仓库访问权限后，将下文和仓库地址交给本地 Agent：

```text
请帮我配置这个 Account 私有仓库：https://github.com/Milozpace/Account-Preview 。
前提：你已能以我授权的身份读取该私有仓库、执行本机命令；请使用已有本机 Git/托管平台认证，不要在聊天中接收密码、令牌或配对截图。
先读 README.md、AGENTS.md 和 docs/开始使用.md，完成环境检查、独立空账本初始化、匹配预览版的电脑/Android 包安装和核验。
先询问我的预算生效月份和四池额度；初始滚存默认零。不要复制他人参数和历史规则，证据不足保留 Unknown。
凭据由我在 App 原生设置界面输入，配对由我在设备操作；需要我操作时给具体步骤。
本次先完成配置，真实账单导入和云端数据发布按我的实际授权进行。后续若授权开发，验证后主动提交相关代码。分别报告已完成、待我操作和未验收事项。
```

日常使用见[工作说明](docs/工作说明.md)，个人学习见[规则与学习](docs/规则与学习.md)，开发和反馈见[开发指南](docs/开发指南.md)与[反馈指南](docs/反馈指南.md)。
