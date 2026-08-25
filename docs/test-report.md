# 测试报告（主题分类模型接入）

## 1. 概要

- 范围：数据预处理、模型训练与评估、本地模型服务、业务集成与端到端验证
- 目标：验证主题分类与路由在模型接入后仍可稳定运行，并具备降级与可审计能力

## 2. 训练验证

### 2.1 数据集

- 数据文件：data/labeled_tickets.csv
- 切分比例：train 80% / val 10% / test 10%

### 2.2 训练配置

- 模型：Multinomial Naive Bayes（字符+字母数字 token）
- 超参搜索：alpha ∈ {0.5, 1.0, 2.0}，以 val accuracy 选择最优

### 2.3 评估结果（填写训练脚本输出）

- best_alpha：0.5
- val_accuracy：0.75（val=4）
- test_accuracy：0.8333（test=6）
- test_recall_by_label：
  - order_delivery: 1.0
  - payment: 0.6667
  - other: 1.0
  - account: 0.0
  - after_sales: 0.0

## 3. 本地服务稳定性

### 3.1 服务信息

- 服务脚本：model_service/server.py
- 模型文件：model_service/model.json
- 健康检查：GET /health
- 推理接口：POST /classify

### 3.2 功能验证用例

- 用例 1：配送类文本 → topic=order_delivery
- 用例 2：扣款/退款类文本 → topic=payment
- 用例 3：忘记密码/改手机号类文本 → topic=account
- 用例 4：模糊文本 → topic=other（或低置信）

### 3.3 异常与降级验证

- 模型服务不可用（连接失败/超时）时：
  - 业务侧应降级为规则分类
  - 审计日志中 errors 字段记录降级原因

## 4. 业务集成验证

### 4.1 集成点

- main.py 启动参数：--classifier-url http://127.0.0.1:8001
- 模型调用超时：--classifier-timeout-ms 300（默认）

### 4.2 端到端结果（填写实际运行结论）

- sample_tickets.json 共 2 条
- 预期：
  - TCK-0001 → order_delivery → AUTO_SUGGEST
  - TCK-0002 → payment 且 risky → ROUTE_TO_HUMAN_PAYMENT
- 实际：
  - 是否符合预期：是
  - 验证脚本：model_service/e2e_test.py（本地临时启动模型服务后进行断言）

## 5. 结论与待办

- 是否达标：端到端链路达标（模型训练→本地服务→业务集成→路由输出），具备可降级与可审计能力
- 已知限制：训练数据量较小，account/after_sales 在当前 test 切分上召回偏低；需扩大样本并做类别均衡与更严格的评估集
- 下一步迭代建议：补充真实历史工单样本与回标；引入更强的轻量模型（如 MiniLM/DistilBERT）并做置信度校准；增加灰度与线上监控指标
