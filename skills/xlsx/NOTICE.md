# 来源、许可证与本地修改

Source: Anthropic, https://github.com/anthropics/skills
Pinned commit: e4bb5d960d87126ff3b3a8af1a104b7db67560ba
Upstream directory: skills/xlsx
License: Apache License 2.0，完整原文保存在 LICENSE.txt，未修改。

Modified by DataQuery Agent: skills/xlsx/SKILL.md 和 references/format-guidance.md 是本地受限文本适配。保留报告格式、来源和验证注意事项；移除执行命令、依赖安装指令、自动脚本流程和预装环境假设。自研项目既有代码及经营指标不因本适配变更许可证。

upstream/SKILL.md 保留固定版本原始指令，未修改。upstream/THIRD_PARTY_NOTICES.md 保留仓库原始第三方声明供追溯；其中提及的软件、媒体和字体没有随本适配打包。UPSTREAM.json 记录53项上游文件及Hash。

上游12个Python文件和39个XSD只在临时目录完成来源Hash核对；Python做静态副作用筛查，XSD未进行语义审核，不进入本地Skill包，不注册、不导入、不执行。脚本运行安全、依赖许可证及跨平台可执行性没有通过本轮验收。

Registry 的 reviewed/enabled 仅表示本地适配文本可通过受控接口加载，allowed_tools 为空。Gemini/LangGraph没有新增执行路径或模型调用。Excel生成、公式重算、Artifact与HTTP下载均未实现。后续工具接入需要独立实现和验收。
