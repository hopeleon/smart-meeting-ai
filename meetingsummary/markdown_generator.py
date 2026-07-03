"""Markdown report generation from structured meeting summary data."""

from typing import Any


def _render_tldr(data: dict[str, Any]) -> str:
    tldr = data.get("tldr", "")
    if not tldr:
        return ""
    return f"> **TL;DR**：{tldr}\n\n"


def _render_meeting_header(meeting: dict[str, Any]) -> str:
    title = meeting.get("title", "Meeting Summary")
    meeting_type = meeting.get("meeting_type", "")
    lines = [
        f"# {title}",
        "",
    ]
    if meeting_type:
        lines.append(f"**会议类型**：{meeting_type}")
        lines.append("")
    lines.extend([
        "## 会议信息",
        "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| 日期 | {meeting.get('date', 'N/A')} |",
        f"| 时间 | {meeting.get('time', 'N/A')} |",
        f"| 地点 | {meeting.get('location', 'N/A')} |",
        f"| 主持人 | {meeting.get('host', 'N/A')} |",
        f"| 参会人员 | {', '.join(meeting.get('attendees', [])) or 'N/A'} |",
        f"| 议程 | {', '.join(meeting.get('agenda', [])) or 'N/A'} |",
        "",
    ])
    return "\n".join(lines)


def _render_discussion_points(points: list[dict[str, Any]]) -> str:
    if not points:
        return "## 讨论要点\n\n无\n"
    lines = ["## 讨论要点", ""]
    for i, p in enumerate(points, 1):
        topic = p.get("topic", "N/A")
        speaker = p.get("speaker", "Unknown")
        summary = p.get("summary", "")
        conclusion = p.get("key_conclusion", "")
        lines.append(f"### {i}. {topic}")
        lines.append(f"- **发言人**: {speaker}")
        lines.append(f"- **摘要**: {summary}")
        if conclusion:
            lines.append(f"- **结论**: {conclusion}")
        quote = p.get("source_quote")
        if quote and quote.strip():
            lines.append(f"- **原文引用**: > {quote}")
        lines.append("")
    return "\n".join(lines)


def _render_decisions(decisions: list[dict[str, Any]]) -> str:
    if not decisions:
        return "## 决策事项\n\n无\n"
    lines = ["## 决策事项", ""]
    for i, d in enumerate(decisions, 1):
        desc = d.get("description", "N/A")
        responsible = d.get("responsible", "N/A")
        deadline = d.get("deadline", "N/A")
        rationale = d.get("rationale", "")
        lines.append(f"{i}. **{desc}**")
        lines.append(f"   - 负责人: {responsible}")
        lines.append(f"   - 截止日期: {deadline}")
        if rationale:
            lines.append(f"   - 决策依据: {rationale}")
        quote = d.get("source_quote")
        if quote and quote.strip():
            lines.append(f"   - 原文引用: > {quote}")
        lines.append("")
    return "\n".join(lines)


def _render_action_items(items: list[dict[str, Any]]) -> str:
    if not items:
        return "## 行动项\n\n无\n"
    priority_labels = {"P0": "🔴P0", "P1": "🟠P1", "P2": "🟡P2", "P3": "🟢P3"}
    lines = ["## 行动项", ""]
    for i, item in enumerate(items, 1):
        task = item.get("task", "N/A")
        assignee = item.get("assignee", "N/A")
        due = item.get("due", "N/A")
        priority = item.get("priority", "")
        line = f"{i}. **{task}**"
        if priority:
            label = priority_labels.get(priority, priority)
            line += f"  {label}"
        lines.append(line)
        lines.append(f"   - 负责人: {assignee}")
        lines.append(f"   - 截止日期: {due}")
        quote = item.get("source_quote")
        if quote and quote.strip():
            lines.append(f"   - 原文引用: > {quote}")
        lines.append("")
    return "\n".join(lines)


def _render_issues_risks(items: list[Any]) -> str:
    if not items:
        return "## 问题与风险\n\n无\n"
    lines = ["## 问题与风险", ""]
    for item in items:
        if isinstance(item, str):
            lines.append(f"- {item}")
        elif isinstance(item, dict):
            desc = item.get("description", "N/A")
            severity = item.get("severity", "N/A")
            mitigation = item.get("mitigation", "暂无")
            lines.append(f"- **{desc}**")
            lines.append(f"  - 严重程度: {severity}")
            lines.append(f"  - 缓解措施: {mitigation}")
            quote = item.get("source_quote")
            if quote and quote.strip():
                lines.append(f"  - 原文引用: > {quote}")
    lines.append("")
    return "\n".join(lines)


def _render_next_meeting(next_mtg: dict[str, Any]) -> str:
    lines = [
        "## 下次会议",
        "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| 日期 | {next_mtg.get('date', 'TBD')} |",
        f"| 时间 | {next_mtg.get('time', 'TBD')} |",
        f"| 地点 | {next_mtg.get('location', 'TBD')} |",
        f"| 暂定议程 | {next_mtg.get('tentative_agenda', 'TBD')} |",
        "",
    ]
    return "\n".join(lines)


def _render_completeness_footer(completeness_data: dict[str, Any]) -> str:
    lines = [
        "## 完整性检查",
        "",
    ]
    score = completeness_data.get("coverage_score", 0)
    threshold = completeness_data.get("threshold", 80.0)
    passed = completeness_data.get("passed", False)
    status = "通过" if passed else "未通过"
    lines.append(
        f"- **综合覆盖率**: {score:.1f}% （阈值 {threshold:.0f}%，{status}）"
    )

    agenda = completeness_data.get("agenda_check", {})
    if agenda:
        covered = agenda.get("covered", 0)
        total = agenda.get("total", 0)
        lines.append(f"- **议程覆盖**: {covered}/{total}")
        missing_agenda = agenda.get("missing", [])
        if missing_agenda:
            lines.append(f"  - 缺失: {', '.join(missing_agenda)}")

    speaker = completeness_data.get("speaker_check", {})
    if speaker:
        covered = speaker.get("covered", 0)
        total = speaker.get("total", 0)
        lines.append(f"- **发言人覆盖**: {covered}/{total}")
        missing_speaker = speaker.get("missing", [])
        if missing_speaker:
            lines.append(f"  - 缺失: {', '.join(missing_speaker)}")

    section = completeness_data.get("section_check", {})
    if section:
        empty = section.get("sections_empty", [])
        missing_sec = section.get("sections_missing", [])
        if empty:
            lines.append(f"- **空白章节**: {', '.join(empty)}")
        if missing_sec:
            lines.append(f"- **缺失章节**: {', '.join(missing_sec)}")

    assessment = completeness_data.get("overall_assessment", "")
    if assessment:
        lines.append(f"- **总体评价**: {assessment}")

    lines.append("")
    return "\n".join(lines)


def generate_markdown(
    data: dict[str, Any],
    completeness_data: dict[str, Any] | None = None,
) -> str:
    """Render a parsed meeting summary dict as a Markdown report.

    Args:
        data: Parsed meeting summary dictionary.
        completeness_data: Optional completeness check results.

    Returns:
        Markdown-formatted string.
    """
    sections: list[str] = [
        _render_tldr(data),
        _render_meeting_header(data.get("meeting", {})),
        _render_discussion_points(data.get("discussion_points", [])),
        _render_decisions(data.get("decisions", [])),
        _render_action_items(data.get("action_items", [])),
        _render_issues_risks(data.get("issues_risks", [])),
        _render_next_meeting(data.get("next_meeting", {})),
    ]
    if completeness_data is not None:
        sections.append(_render_completeness_footer(completeness_data))
    return "\n".join(sections)
