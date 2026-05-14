# LLM 会议总结服务

## 接口说明

本服务提供会议文本总结功能，包含两种模式：

### 1. 阶段总结

```
POST /summarize/period
Content-Type: application/json

{
  "meeting_id": "uuid",
  "transcript_lines": [
    {"speaker": "发言人A", "text": "...", "start": 0.0, "end": 3.5}
  ]
}

Response:
{
  "bullet_points": ["要点1", "要点2", "要点3"]
}
```

### 2. 最终总结

```
POST /summarize/final
Content-Type: application/json

{
  "meeting_id": "uuid",
  "all_transcript_lines": [...],
  "period_summaries": [...]
}

Response:
{
  "overview": "会议概述...",
  "key_decisions": ["决策1", "决策2"],
  "action_items": [
    {
      "content": "任务描述",
      "assignee": "负责人",
      "due_date": "2026-05-20"
    }
  ]
}
```

## 接入指南

算法团队需实现 `app/interface.py` 中的函数，签名不可修改。

### 模型接入方式

1. **本地模型**：在 requirements.txt 中添加推理框架依赖（如 transformers、vllm）
2. **外部 API**：在环境变量中配置 API Key，通过 HTTP 调用
