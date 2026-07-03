# 文档总览

更新时间：2026-07-03

这里是 Smart Meeting AI 当前版本的文档入口。项目现在包含两条主线：智能会议记录和“老记”一句话待办。

## 产品文档

| 文档 | 内容 |
| --- | --- |
| [会议记录功能说明](./meeting-feature-spec.md) | 会议实时转写、说话人管理、总结、导出、企业化优化方向 |
| [老记功能说明](./laoji-feature-spec.md) | 一句话代办、短语音 ASR、Qwen 8B 理解、追问补充、日历写入 |
| [老记输入输出样例](./laoji-examples.json) | 典型输入、期望解析结果、追问补充和 ASR 样例 |

## 工程文档

| 文档 | 内容 |
| --- | --- |
| [目录规范](./project-structure.md) | 当前目录含义、归档规则、后续放置约定 |
| [运行与排障手册](./runbook.md) | 当前服务器上的启动命令、访问地址、健康检查和常见问题 |
| [系统架构](./architecture.md) | 原始系统架构与模块拆分说明 |
| [API 契约](./api-contracts.md) | REST/WebSocket 接口约定 |
| [开发指南](./development-guide.md) | 开发环境与协作说明 |
| [集成指南](./integration-guide.md) | 算法、前端、部署等团队接入参考 |
| [部署设置指南](./SETUP_GUIDE.md) | 原始环境搭建与部署说明 |

## 评估报告

| 文档 | 内容 |
| --- | --- |
| [企业实时 ASR 三模式评估报告](./reports/enterprise-realtime-asr-evaluation-2026-07-02.md) | FunASR、Qwen ASR、混合模式测试结论和企业化差距 |
| [HTML 版评估报告](./reports/enterprise-realtime-asr-evaluation-2026-07-02.html) | 便于浏览器预览和展示 |

## 推荐阅读顺序

1. 先看 [README.md](../README.md) 了解项目入口。
2. 如果关注会议记录，看 [会议记录功能说明](./meeting-feature-spec.md) 和 [运行与排障手册](./runbook.md)。
3. 如果关注老记，看 [老记功能说明](./laoji-feature-spec.md) 和 [老记输入输出样例](./laoji-examples.json)。
4. 如果准备继续开发或拆分 App，看 [目录规范](./project-structure.md) 和 [API 契约](./api-contracts.md)。
