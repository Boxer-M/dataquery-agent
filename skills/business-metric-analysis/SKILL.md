---
name: business-metric-analysis
description: 为明确要求多指标、跨期、趋势、排行或对比的电商经营指标分析提供计划约束。简单单指标查询沿用原路径。
license: UNLICENSED; project-authored, no standalone redistribution license declared.
---

# 电商经营指标分析

本 Skill 是受控分析的指令骨架。阶段 A 仅注册、读取和候选识别，不执行查询。

- 正式指标身份、定义和状态以运行时 Metric Catalog 为准，不在此复制公式或创建别名。
- 时间窗口、维度、筛选及观察截止以原问题和现有能力契约为准；缺失口径应澄清。
- 候选计划必须经过原 Business Plan Validator、Capability 与执行前 Owner 复核。
- 数值只能来自现有 Metric Tool 的真实结果，保持 Decimal 精度、事件时间、比率分子分母和来源。
- 销售金额、成功支付金额及退货记录金额分别陈述；不能据此补造净收入、利润或经营原因。
- partial 和工具失败应保留已验证结果并说明缺口。候选识别不赋予执行、网络或文件权限。

需要确认模块边界时，按需读取已登记资源 `contracts`：
[现有能力引用](references/contracts.md)。
