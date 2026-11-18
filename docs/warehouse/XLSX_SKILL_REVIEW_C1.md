# XLSX Skill 固定版本审核（C1）

## 来源与结论

基线为阶段 B 已合并的 main `f839baa`。固定 Anthropic skills 提交 **e4bb5d960d87126ff3b3a8af1a104b7db67560ba**，来源目录为：

https://github.com/anthropics/skills/tree/e4bb5d960d87126ff3b3a8af1a104b7db67560ba/skills/xlsx

审核者为 Codex，范围为静态来源核验、指令改写与权限边界检查，不代表人工业务审核、法律意见或脚本运行验收。下载来自固定 SHA 的官方原始文件，并逐项核对 Git Blob SHA，再记录 SHA-256。审核没有导入或执行上游 Python，没有运行重算、Office 或宏，没有安装依赖，也没有发送项目资料给外部模型。

结论：只批准本地适配后的文本指令及三个白名单文本资源通过现有 SkillRegistry 加载。没有批准上游运行环境或脚本。没有 Excel生成、Artifact、API或下载能力。

## 文件与许可

| 本地文件 | 用途及处理 |
| --- | --- |
| skills/xlsx/SKILL.md | 本地受限适配入口，明确标注修改；删除执行命令及预装环境假设 |
| skills/xlsx/LICENSE.txt | 原目录完整 Apache-2.0许可证，逐字节保留 |
| skills/xlsx/NOTICE.md | 固定来源、修改范围、分发及未支持能力声明 |
| skills/xlsx/references/format-guidance.md | 改写的报告格式、数值、来源与验证指导 |
| skills/xlsx/upstream/SKILL.md | 未修改的上游原始指令，只用于审计，未登记为可加载资源 |
| skills/xlsx/upstream/THIRD_PARTY_NOTICES.md | 未修改的上游仓库声明，仅作来源存档，未登记为可加载资源 |
| skills/xlsx/UPSTREAM.json | 上游53项文件的Git Blob、SHA-256、大小和保留/排除决策 |

目录许可证是 Apache-2.0。保留完整许可证和来源声明，在两个本地改写文件中标注修改。不将其扩展为现有项目代码或经营指标的许可证。仓库级第三方声明涉及软件及字体，本轮只存档声明文本，没有打包其中提及的软件、媒体或字体。后续若引入运行依赖，须按选定版本单独锁版并审核许可证。

上游目录包含1份指令、1份许可证、12个Python文件及39个XSD，合计53项。Python进行了导入/执行入口与副作用静态风险筛查，XSD仅做目录及Hash核对，没有宣称逐项语义审核。12个Python和39个XSD都没有复制进项目Skill包，不存在脚本注册、调用或通用解释器入口。

## 发现与适配决策

| 上游内容/位置 | 静态证据 | 本轮决策 |
| --- | --- | --- |
| SKILL.md | 假定openpyxl/pandas/markitdown预装，缺失时建议安装 | 本地入口删除安装和预装承诺，不安装依赖 |
| SKILL.md / scripts/recalc.py | 要求运行LibreOffice重算，脚本创建临时profile和Basic宏并启动子进程 | 不复制、不运行；仅保留“公式缓存不等于业务正确”的指导 |
| scripts/recalc.py | errors_found不一定导致非零退出码，输出JSON还需独立判断 | 记录为未来实现注意事项，不声称重算成功 |
| scripts/office/soffice.py | 复制环境变量，运行soffice；受限环境分支编译gcc共享库并使用LD_PRELOAD | 全部排除，不能将环境兼容处理当成授权 |
| office/validate.py、validators及helpers | 文件/ZIP/XML读取和部分修复写回，额外引入defusedxml/lxml | 排除，未审核任何任意上传工作簿执行 |
| 上游财务默认格式 | 包含美元及通用财务假设 | 不继承为项目币种或业务定义；只使用宿主已验证元信息 |

依赖说明：上游指令提及 openpyxl、pandas、markitdown、LibreOffice；静态脚本还引用 defusedxml、lxml，并可能需要 gcc/平台超时工具。上游Skill没有为这些依赖提供本项目可直接采用的固定版本集合。本轮未安装、未锁版、未批准使用这些依赖。现有 `.venv` 中上述五个Python包均未发现；宿主外部环境存在某个工具也不构成项目依赖或Docker可用性的证明。

## Registry与权限

xlsx从 `0.0.0-pending / unreviewed / disabled` 转为本地版本 `0.1.0-c1 / reviewed / enabled`，登记固定SHA、Apache-2.0及入口Hash。这里的 enabled与候选 available仅表示文本可以受控加载，不能解释为可生成报告。`allowed_tools=[]`。

仅允许资源别名 `format-guidance`、`provenance`、`license`，每次读取继续校验适配入口和资源Hash。原始指令、原仓库声明、UPSTREAM.json及任意路径都不能通过资源接口读取。现有路径穿越、符号链接、文件大小及篡改拒绝逻辑不变。

本轮不修改Registry实现、Unified Agent、Planner、Capability、SQL规则、模型预算、API、Docker或GitHub Actions。阶段B只选择经营Skill，仍不自动加载或注入XLSX指令。

```python
from dataquery_agent.skill_registry import SkillRegistry
registry = SkillRegistry()
registry.load_skill_instructions("xlsx")
registry.load_skill_resource("xlsx", "format-guidance")
```

## 离线验收与边界

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_skill_registry.py tests/test_skill_business.py tests/test_xlsx_skill_review.py -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
```

测试覆盖锁版与原件完整性、合法加载、资源白名单、指令和资源篡改、XLSX目录/文件符号链接、未选中不加载、不自动注入、原业务Skill和Fast Path。阶段A关于未审核和未锁版的负例改为显式构造该非法状态，继续拒绝；不是删除保护以适应新注册状态。

默认测试不联网，不调用Gemini，不需要真实数据库或Office环境。未执行 Excel生成、公式重算、跨平台Office运行或端到端报告验收；这些能力留待后续明确授权的阶段。

本轮实测：相关离线专项54通过、1项PostgreSQL用例按环境门控跳过；Python全量212通过、80项环境门控跳过。适配入口通过Skill格式校验，新增文件通过密钥/私有路径/大小检查。没有真实Gemini、Office或脚本调用，没有重新运行部署及浏览器验收。提交体积主要来自必须原样保留的许可证、原始文本声明和53项来源清单；`.gitattributes` 对原始存档关闭换行转换并保留既有行尾空格，确保Hash可复核，不改变运行配置。
