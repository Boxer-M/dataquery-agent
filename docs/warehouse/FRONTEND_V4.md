# Frontend v4 首屏优化与图表契约核查

启动方式沿用 [本地工作台](FRONTEND_V1.md)。本轮对查询结果和历史 Trace 使用按需加载。真实分组结果继续通过现有表格展示。

## 真实 PostgreSQL 对账

通过 FastAPI 查询，再用对 `ods.orders` 的独立只读 `COUNT(*)` 对账。统计以 `ordered_at` 为准，包含取消订单。

| 查询 | API 与 ODS 一致的结果 | 查询 ID |
| --- | --- | --- |
| 2025 年 1 月按渠道统计订单量 | mobile 685、partner 207、phone 83、web 1,337 | `6b056276-3c3b-4f56-a694-de8492fe0aa6` |
| 2025 年订单量，随后追问“按月拆分” | 1 月 2,312、2 月 2,049、3 月 2,225、4 月 2,251、5 月 2,306、6 月 2,275、7 月 2,350、8 月 2,236、9 月 2,262、10 月 2,318、11 月 3,437、12 月 4,128 | `3ad5974a-48c9-4044-908b-4b4d131ba55a` |

两组实际响应均未截断，定义含 `order_count`，数值是字符串。月度结果有 `month` 和每行的 `start_date`、`end_date`。渠道结果只有通用 `bucket`，统计区间只出现在回答文本。两组都缺少正式名称和结构化单位，指标目录也没有专门的单位字段。回答正文不作为图表数据来源。

## 最小 API 扩展建议

给现有响应增加可选的 `visualization` 元数据，由后端从已验证指标和本次受控请求生成。继续使用原有 `rows` 和 `rows_truncated`。

- 指标提供 `metric_id`、正式名称、经过审核的单位与来源模型。
- 维度提供字段名、时间或类别类型及排序规则，区分 `month` 和 `bucket` 的具体含义。
- 统计窗口提供结构化起止日期，标明结束日右开。
- 首版可限定为一个指标和一个维度。前端使用原始 Decimal 字符串显示工具提示，超安全整数范围或小数精度不适配时保留表格。空结果、单行、混合单位、截断或缺字段同样留在原有展示。分组在单一回复内处理。

## 构建实测

以 Frontend v3 合并提交 `02866f6` 为基线，使用同一依赖和 Vite 生产构建。首屏统计入口 JS 和其静态依赖，gzip 列取自 Vite 构建输出。

| JavaScript | v3 | v4 |
| --- | ---: | ---: |
| 首屏 | 946.90 kB | 508.76 kB |
| 首屏 gzip | 300.43 kB | 166.90 kB |
| 所有异步功能均加载后 | 946.90 kB | 952.16 kB |
| 按需查询结果 | — | 367.81 kB |
| 按需历史 Trace | — | 75.57 kB |

Rolldown 模块清单显示，入口的大头是 React DOM 和 Ant Design 基础控件。查询结果块主要包含 Ant Design 表格及相关依赖，历史块主要包含 Drawer、Collapse 和 Timeline 相关代码。首屏 JS 缩小约 46.3%，全部功能加载后 JS 总量增加约 5.26 kB。Vite 仍提示入口块超过 500 kB。

## 回归验收

真实 Chrome 在两个本地地址上确认首屏未请求结果和历史模块，收到首条回答后加载结果模块，打开历史时才加载 Trace 模块。渠道表格逐行匹配 API 的四条结果。指标卡、两条正式引用、澄清追问、Trace 一致性及断网重试均通过。精度、截断和失败保护继续由受控浏览器响应验证。

TypeScript、生产构建和两项代理测试通过。Python 全量回归共运行 186 项，185 项通过，1 项可选 Gemini 测试跳过。实测伪造 Host、外部 Origin 及两个本地地址之间的交叉 Origin 均被拒绝。浏览器请求和构建产物均未检出 Bearer 令牌。

可在两个本地服务运行时复验，并保存真实渠道结果截图。

```sh
npm --prefix frontend test
npm --prefix frontend run build
WORKSPACE_GROUPED_SCREENSHOT=/tmp/dataquery-grouped.png npm --prefix frontend run test:browser
WORKSPACE_ORIGIN=http://localhost:5173 npm --prefix frontend run test:browser
```
