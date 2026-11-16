# Frontend v5 订单量图表

在本地启动工作台后，可先查询“2025年订单量是多少？”，再追问“按月拆分”查看折线图。发送“查询2025年1月按渠道统计订单量”可查看柱状图。使用结果卡片上的“图表”和“表格”按钮切换。Tooltip 展示指标名称、维度、原始数值和单位。[本地启动说明](FRONTEND_V1.md)

适配器根据 [图表契约](CHART_DATA_CONTRACT_V1.md) 读取指标、单位、日期、维度、原始行索引及数值路径。它校验索引覆盖、维度顺序和数值类型，只有非负安全整数可以进入本轮订单量数值轴。工具提示和表格使用原始字符串。无效契约、高精度小数、大整数、截断及历史摘要继续使用现有展示。ECharts 6.1.0 仅注册线图、柱状图、网格、Tooltip 和 Canvas 渲染器，异步块在首次出现合法图表时加载。[Apache ECharts 按需导入说明](https://echarts.apache.org/handbook/en/basics/import/)

## 真实对账

Chrome 测试读取 ECharts 实际图形序列，逐点与 API 的 `row_order`、行维度和原始 `value` 对比。再与 PostgreSQL 中独立的 `ods.orders` 只读计数对照。两个本地地址均有 16/16 行一致。

| 月份 | API / 图表 / ODS 三者一致 | 渠道 | API / 图表 / ODS 三者一致 |
| --- | ---: | --- | ---: |
| 2025-01 | 2,312 | mobile | 685 |
| 2025-02 | 2,049 | partner | 207 |
| 2025-03 | 2,225 | phone | 83 |
| 2025-04 | 2,251 | web | 1,337 |
| 2025-05 | 2,306 | | |
| 2025-06 | 2,275 | | |
| 2025-07 | 2,350 | | |
| 2025-08 | 2,236 | | |
| 2025-09 | 2,262 | | |
| 2025-10 | 2,318 | | |
| 2025-11 | 3,437 | | |
| 2025-12 | 4,128 | | |

`127.0.0.1` 月度查询 ID 为 `e3050a42-b7da-4a78-ae5e-eb1a0a645cbe`，渠道查询 ID 为 `4127078f-8acf-495a-bb7a-d6f728f04ac2`。图表与表格切换产生 0 次额外聊天请求。

## 生产构建体积

使用相同环境分别构建 `3d05511` 和本轮版本。首屏统计入口 JS 及其静态依赖。gzip 为 Vite 输出的各 JS 块体积之和。

| JavaScript | v4 | v5 |
| --- | ---: | ---: |
| 首屏 | 508.77 kB | 508.77 kB |
| 首屏 gzip | 166.90 kB | 167.22 kB |
| 全部异步功能加载 | 952.16 kB | 1,471.99 kB |
| 全部异步功能加载 gzip | 304.73 kB | 481.44 kB |
| 图表异步块 | — | 517.07 kB |

Vite 仍对超过 500 kB 的块给出体积提示，本轮对应图表异步块。折线横轴在可用宽度不足时会省略部分刻度标签，数据点仍完整，逐行值可在表格中核对。

## 回归与复现

TypeScript、生产构建和 4 项前端测试通过。Python 全量回归 186 项通过、1 项可选 Gemini 测试跳过。两种本地地址完成真实 Chrome 联调，覆盖指标卡、引用、澄清、History、Trace 与断网重试。实测代理拒绝非法 Host 及跨来源请求，构建文件令牌检查通过。

```sh
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
WORKSPACE_MONTH_SCREENSHOT=/tmp/dataquery-month.png WORKSPACE_CHANNEL_SCREENSHOT=/tmp/dataquery-channel.png npm --prefix frontend run test:browser
WORKSPACE_ORIGIN=http://localhost:5173 npm --prefix frontend run test:browser
```

适配器单元测试直接加载 TypeScript，使用支持类型剥离的 Node.js，本次为 25.8.1。浏览器测试要求本机 Chrome 和已启动的 FastAPI、Vite。
