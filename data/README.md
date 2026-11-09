# 数据目录

`data/raw/` 保存从 Kaggle 官方来源下载的原始压缩包、官方元数据和原样解压 CSV。该目录被 Git 忽略，文件只读使用，不进行覆盖式清洗。

## 官方下载复现

在项目根目录执行：

```bash
mkdir -p data/raw
curl --fail --location --show-error \
  --output data/raw/kaggle_dataset_metadata.json \
  https://www.kaggle.com/api/v1/datasets/view/olistbr/brazilian-ecommerce
curl --fail --location --retry 3 --show-error \
  --output data/raw/olist_brazilian_ecommerce_kaggle.zip \
  https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce
unzip -n data/raw/olist_brazilian_ecommerce_kaggle.zip -d data/raw
```

本次下载公开数据时没有遇到 Kaggle 登录阻塞。若官方策略以后要求认证，应在本机按 Kaggle 官方说明配置凭据；不得把凭据写入仓库，也不得擅自切换第三方镜像。

本次已验证的压缩包：

```text
size:   44717580 bytes
sha256: 967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784
```

若官方数据更新，大小或 SHA-256 可能变化。此时应保留新下载日期和官方版本元数据，重新运行审计，不能为了匹配旧哈希而寻找非官方副本。

## 运行审计

```bash
PYTHONPATH=src .venv/bin/python -m dataquery_agent.data_audit \
  --raw-dir data/raw \
  --output reports/d1_data_audit.json
```

入口是 `dataquery_agent.data_audit.main`。`build_audit` 负责组织 9 个文件、关系和语义检查，`audit_csv` 逐行统计单文件，最终输出 JSON 报告。脚本只读取 `data/raw/`，不会清洗、去重或覆盖原始文件。

## 运行测试

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

测试使用临时 CSV，仅验证空值、重复键、非法日期和金额解析逻辑，不属于 Olist 业务数据。
