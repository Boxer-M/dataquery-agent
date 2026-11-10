# DataQuery Agent 开发约束

- 本仓库只开发 DataQuery Agent；SQL Copilot 必须保留为另一个独立项目和独立 Git 仓库。
- 开始任务前读取 `README.md`、正式设计 `docs/warehouse/README.md`、本地存在时的 `PROJECT_PLAN.md`、适用的 `AGENTS.md` 和真实 Git 状态。`PROJECT_PLAN.md` 与 `LEARNING_MAP.md` 仅保存在 Mac 本地，不纳入 Git。
- 使用项目 `.venv` 中的 Python 3.12，保留包名 `src/dataquery_agent`，不修改全局 Python。
- 按业务主题或完整批次完成一个可验收里程碑；数仓优先于 Agent，不按单表零散建模或创建 PR。先完整设计和说明实施分组，再实现、测试、复盘和提交；当前路线见 `docs/warehouse/IMPLEMENTATION_BATCHES.md`。
- 实现前用通俗语言说明目标、输入、输出、核心调用链和风险；完成后解释关键代码及面试讲法，不能只交付大段代码。
- `data/raw/` 中的公开源数据只读且不提交；不得把测试样本或模拟数据冒充真实业务数据。
- 计划、已完成和未验证事项必须明确区分；测试结果、指标和 Git 历史必须来自真实执行。
- 不提交 `.env`、API Key、数据库密码、令牌或个人隐私；`.env.example` 只能保留安全占位。
- 优先使用标准库和最小依赖；新增依赖前说明用途、版本兼容性和替代方案。
- 提交前检查测试、diff、密钥、大文件和原始数据；未经明确要求不推送、不改写提交历史。
- Git 提交使用 Conventional Commits 风格，不在提交信息中使用 D1/D2 等进度编号；通常每次变更不超过 200 行，首次数据落盘等明确例外必须说明原因。
- 每次结束按交接文件中的统一报告格式汇报，并只提出下一步最小任务。
