# PoC 样例输入与输出

## 样例输入（mock tickets）

文件：[sample_tickets.json](file:///c:/Users/Tolia/Documents/GitHub/AI-EnterExam-Task2/data/sample_tickets.json)

### Ticket 1（Happy，低风险）

```json
{
  "ticket_id": "TCK-0001",
  "channel": "app",
  "created_at": "2026-08-25T10:00:00+03:00",
  "user_id": "U-10001",
  "order_id": "20260825-001",
  "text": "订单一直显示骑手已取餐但40分钟没动了，怎么催单？"
}
```

### Ticket 2（Risky，必须人工）

```json
{
  "ticket_id": "TCK-0002",
  "channel": "email",
  "created_at": "2026-08-25T10:01:00+03:00",
  "user_id": "U-20002",
  "order_id": "20260825-002",
  "text": "我被扣了两次钱订单还取消了，要求立刻退款！手机号13800138000。"
}
```

## 样例输出（期望结构）

### Ticket 1（Happy）期望输出

```json
{
  "ticket_id": "TCK-0001",
  "topic": "order_delivery",
  "risk_level": "safe",
  "confidence": 0.8,
  "evidence_topk": [
    {
      "source": "kb",
      "doc_id": "KB-001",
      "title": "配送超时如何催单与处理",
      "score": 0.25
    }
  ],
  "action": "AUTO_SUGGEST",
  "reason": "safe_high_confidence",
  "draft_reply": "已收到反馈。建议先在订单页点击“联系骑手/催单”，若长时间无更新可在售后入口发起申诉。参考：配送超时如何催单与处理"
}
```

### Ticket 2（Risky）期望输出

```json
{
  "ticket_id": "TCK-0002",
  "topic": "payment",
  "risk_level": "risky",
  "confidence": 0.75,
  "evidence_topk": [
    {
      "source": "kb",
      "doc_id": "KB-006",
      "title": "扣款异常/重复扣款如何处理",
      "score": 0.22
    }
  ],
  "action": "ROUTE_TO_HUMAN_PAYMENT",
  "reason": "risky_policy",
  "draft_reply": null
}
```

## 审计日志（JSONL）期望

- 路径：`logs/audit.jsonl`
- 每处理一条 ticket 追加 1 行 JSON
- 关键字段包含：`ticket_id`、`input_text_masked`、`topic`、`risk_level`、`confidence`、`action`、`reason`、`evidence`、`versions`
