# Document Ingestion v1

本地命令接受一个明确指定、已获授权的 PDF，输出带页码、版本和 Owner 范围的 JSON。提取结果的审核状态是 `unreviewed`。调用链独立于 RAG 导入、Embedding 和数据库。已检查现有 RAG 的 `Passage.location`、文档版本、Owner 范围、索引及引用校验，提取结果保留后续人工审核和引用绑定所需的原件 SHA-256、页码和提取器版本。

## 选型与安装

| 组件 | 用途与兼容性 | 安装成本与许可证 |
| --- | --- | --- |
| pypdf 7.1.0 | 检查 PDF 对象并提取原生文本。已验证项目 arm64 Python 3.12.13 | 纯 Python wheel 约 0.42 MB。BSD-3-Clause |
| Apple Vision revision 3 / Core Graphics | 本机光栅化与简体中文、英文 OCR。已验证 macOS 15.7.7 和 Apple Swift 6.2 | 使用 macOS 系统框架与 Xcode Command Line Tools，无额外 OCR pip 包或模型下载步骤。Apple 系统框架受 Apple 许可条款约束 |
| PaddleOCR 备选 | PyPI 元数据显示 PaddleOCR 3.7.0 支持 Python 3.12，PaddlePaddle 3.3.1 提供匹配本机的 cp312 macOS arm64 wheel | PaddlePaddle wheel 约 104.5 MB，另有依赖和模型。PaddleOCR 采用 Apache-2.0。本轮核验了 wheel 标签，安装与推理性能未测 |

参考 [pypdf 包元数据](https://github.com/py-pdf/pypdf/blob/main/pyproject.toml)、[Apple Vision API](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)、[PaddleOCR 安装说明](https://www.paddleocr.ai/latest/en/version3.x/installation.html)、[PaddlePaddle 分发文件](https://pypi.org/project/paddlepaddle/3.3.1/#files)及 [PaddleOCR 许可证](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE)。

```sh
.venv/bin/python -m pip install -r requirements-pdf.txt
# 此作用域仅用于运行自制样例。实际调用用受信的调用方 Owner 值。
DATAQUERY_DOCUMENT_OWNER=document-extraction-synthetic-fixture PYTHONPATH=src \
  .venv/bin/python -m dataquery_agent.document_ingestion output/pdf/document-extraction-sample.pdf
DOCUMENT_OCR_TESTS=1 PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -p test_document_ingestion.py -v
```

macOS OCR 运行环境通过 `xcrun swift --version` 检查。样例 PDF 可用 `scripts/make_document_ingestion_sample.py` 重新生成，生成器额外使用 ReportLab、pypdfium2 和 Pillow。提取命令直接使用现成样例时只需项目依赖、pypdf 和系统 OCR。

## 输出与边界

- CLI 使用既有 Owner 哈希规则。相同文件和 Owner 得到稳定且独立的 `document_id`。文件内容的版本为 SHA-256，`extraction_version` 记录输出协议。Owner 值由本地受信调用方提供，输出是本地清单，文件访问由系统权限管理。
- 页码从 1 开始。原生文本达到 12 个字母或数字时保留，不应用 OCR。短文本或空原生层触发 OCR。原生文本未提供稳定边界时 `blocks` 为空。OCR 的行级边界为实际旋转 CropBox 光栅左下角归一化坐标 `[x, y, width, height]`，带 Vision 置信度。
- 每页有 `ok`、`no_text`、`partial` 或 `error`。其中 `no_text` 表示无可提取文本。退出码 0 表示完成且无页错误，2 表示仍有清单可看但部分页面出错，1 表示文件被拒或文档级提取失败。
- 只接受普通本地文件流，10 MiB、40 页、10 万个对象遍历访问、55 秒总墙钟、进程 CPU 时间 45 秒及 8 MiB 结果上限。OCR 逐页渲染，每页最多 600 万像素及 500 个识别块。拒绝加密 PDF、可达的脚本、启动动作和内嵌附件。处理的是同一份私有临时快照，提取进程终止后自动清理。
- 首版按页判断原生层是否充足。文字层和大量图片混排、复杂多列、图片表格和手写内容仍需人工核验。Vision 坐标和置信度可能随系统版本或运行略有变化。未来进入知识库须再绑定经审核的来源、文档版本和引用。

## 实验记录

样例由本项目生成，包含自制中英文字。第 2 页是纯图片，pypdf 确认其原生文本为空。人工打开三页渲染图后，与 [提取 JSON](../../reports/warehouse/document_extraction_sample.json) 核对如下。

| PDF 页 | 页面中英内容 | 实际方式和状态 | 人工差异 |
| --- | --- | --- | --- |
| 1 | 本地样本：订单数量 / Order count: 24 | `native_text` / `ok` | 标题及文本一致 |
| 2 | 扫描样本：订单数量 / Order count: 48 | `ocr` / `ok` | 标题及文本一致，3 个行边界 |
| 3 | 空白 | `ocr` / `no_text` | 无内容 |

三次复跑均无漏字、错字或页码差异。样例 SHA-256 为 `8716be31f972a13bf13b2f2d1daa84913859c6d327e4d501aa8a7df490400c28`。扫描页标题起点的 OCR 归一化横坐标约 0.081，与页面左边距 42/520 一致。复用本机 Swift 模块缓存的三页端到端耗时为 1.23、0.88、0.86 秒，中位数 0.88 秒，`time -l` 报告峰值驻留约 321 MB。单页原生提取单次墙钟 0.23 秒。此处测量的是一份干净的小样例。

启用真实 PostgreSQL 和 macOS OCR 后，全量运行 191 项，190 项通过，1 项可选 Gemini 测试跳过。四项新增测试覆盖 Owner 范围、内容标识、大小与页数上限、脚本、附件、加密文件及三页真实提取。
