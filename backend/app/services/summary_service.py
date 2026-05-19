"""
LLM 总结服务
============
直接调用 meetingsummary/ 模块（Ollama LLM）生成会议总结。

依赖：
- meetingsummary/ 目录（与 backend 同级目录）
- Ollama 服务运行中，config.json 配置正确
"""

import json
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from app.config import settings


# meetingsummary 模块路径（与 backend 同级）
MEETINGSUMMARY_DIR = Path(__file__).resolve().parents[2] / "meetingsummary"


class SummaryService:
    def __init__(self):
        self.meetingsummary_dir = MEETINGSUMMARY_DIR

    async def summarize_period(
        self,
        meeting_id: str,
        transcript_lines: list[dict],
        *,
        output_dir: Optional[Path] = None,
    ) -> dict:
        """
        阶段总结：输入转写文本，返回要点列表。
        调用 meetingsummary 模块，Ollama 生成结构化摘要。

        Args:
            meeting_id: 会议 ID
            transcript_lines: 转写行 [{"speaker": "发言人A", "text": "..."}]
            output_dir: 输出目录，默认临时目录

        Returns:
            {"bullet_points": ["要点1", "要点2", ...]}
        """
        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="summary_period_"))

        transcript_text = self._format_transcript(transcript_lines)
        result = self._call_meetingsummary(
            transcript_text,
            output_dir=output_dir,
            prefix=f"period_{meeting_id[:8]}",
            skip_eval=True,
            skip_completeness=True,
        )

        # 提取 bullet_points（从 summary dict 的 discussion_points 映射）
        bullet_points = []
        if result:
            # 优先用 tldr 作为第一个要点
            if result.get("tldr"):
                bullet_points.append(result["tldr"])
            # discussion_points 转为字符串要点
            for point in result.get("discussion_points", []):
                if isinstance(point, dict):
                    title = point.get("title", "")
                    summary = point.get("summary", "")
                    if title:
                        bullet_points.append(f"【{title}】{summary}" if summary else title)
                elif isinstance(point, str):
                    bullet_points.append(point)

        return {"bullet_points": bullet_points}

    async def summarize_final(
        self,
        meeting_id: str,
        all_transcript_lines: list[dict],
        period_summaries: list[dict],
        *,
        output_dir: Optional[Path] = None,
    ) -> dict:
        """
        最终总结：输入全部转写文本，返回完整会议纪要。
        调用 meetingsummary 模块，Ollama 生成结构化摘要。

        Args:
            meeting_id: 会议 ID
            all_transcript_lines: 全部转写行
            period_summaries: 已有阶段总结列表（可为空）
            output_dir: 输出目录，默认临时目录

        Returns:
            {
                "overview": "会议概述",
                "key_decisions": ["决策1", "决策2"],
                "action_items": [{"content": "...", "assignee": "...", "due_date": "..."}]
            }
        """
        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="summary_final_"))

        transcript_text = self._format_transcript(all_transcript_lines)
        result = self._call_meetingsummary(
            transcript_text,
            output_dir=output_dir,
            prefix=f"final_{meeting_id[:8]}",
            skip_eval=True,
            skip_completeness=True,
        )

        if not result:
            return self._empty_summary()

        # 映射 meetingsummary 输出格式 → API 期望格式
        overview = result.get("meeting", {}).get("summary", result.get("tldr", ""))

        key_decisions = []
        for d in result.get("decisions", []):
            if isinstance(d, dict):
                desc = d.get("description", d.get("decision", str(d)))
                key_decisions.append(desc)
            elif isinstance(d, str):
                key_decisions.append(d)

        action_items = []
        for a in result.get("action_items", []):
            if isinstance(a, dict):
                action_items.append({
                    "content": a.get("task", a.get("description", a.get("content", ""))),
                    "assignee": a.get("assignee"),
                    "due_date": a.get("deadline", a.get("due_date")),
                })
            elif isinstance(a, str):
                action_items.append({"content": a, "assignee": None, "due_date": None})

        return {
            "overview": overview,
            "key_decisions": key_decisions,
            "action_items": action_items,
        }

    def _format_transcript(self, lines: list[dict]) -> str:
        """将转写行格式化为 meetingsummary 可读的文本格式。"""
        parts = []
        for line in lines:
            speaker = line.get("speaker", "未知")
            text = line.get("text", "")
            if text:
                parts.append(f"{speaker}：{text}")
        return "\n".join(parts)

    def _call_meetingsummary(
        self,
        transcript_text: str,
        output_dir: Path,
        prefix: str,
        *,
        skip_eval: bool = False,
        skip_completeness: bool = False,
        skip_actions: bool = True,
    ) -> Optional[dict]:
        """
        调用 meetingsummary/main.py 生成摘要。
        将转写文本写入临时文件，调用 CLI，读取输出的 JSON。
        """
        config_path = self.meetingsummary_dir / "config.json"
        if not config_path.exists():
            print(f"[SummaryService] meetingsummary config.json 不存在: {config_path}")
            return None

        output_dir.mkdir(parents=True, exist_ok=True)

        # 写入临时转写文件
        input_file = output_dir / f"{prefix}_input.txt"
        input_file.write_text(transcript_text, encoding="utf-8")

        cmd = [
            "python",
            str(self.meetingsummary_dir / "main.py"),
            "-i", str(input_file),
            "-o", str(output_dir),
            "--prefix", prefix,
            "--no-map-reduce",  # 阶段总结文本短，直接摘要
            "--skip-completeness" if skip_completeness else "",
            "--skip-eval" if skip_eval else "",
            "--skip-actions" if skip_actions else "",
        ]
        cmd = [c for c in cmd if c]  # 过滤空字符串

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(self.meetingsummary_dir),
            )
            print(f"[SummaryService] meetingsummary stdout:\n{result.stdout}")
            if result.stderr:
                print(f"[SummaryService] meetingsummary stderr:\n{result.stderr}")
        except subprocess.TimeoutExpired:
            print(f"[SummaryService] meetingsummary 超时（180s）")
            return None
        except Exception as e:
            print(f"[SummaryService] meetingsummary 调用失败: {e}")
            return None

        # 读取输出的 JSON
        import datetime
        date_str = datetime.date.today().strftime("%Y%m%d")
        json_path = output_dir / f"{prefix}_{date_str}.json"
        if not json_path.exists():
            print(f"[SummaryService] 摘要文件不存在: {json_path}")
            return None

        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[SummaryService] 解析摘要 JSON 失败: {e}")
            return None

    def _empty_summary(self) -> dict:
        return {
            "overview": "",
            "key_decisions": [],
            "action_items": [],
        }
