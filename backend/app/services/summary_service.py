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
import uuid
from pathlib import Path
from typing import Optional

from app.config import settings


# meetingsummary 模块路径（项目根目录下，与 backend 同级）
MEETINGSUMMARY_DIR = Path(__file__).resolve().parents[3] / "meetingsummary"


# summaries 输出目录（项目内）
SUMMARIES_DIR = Path(__file__).resolve().parents[2] / "summaries"


class SummaryService:
    def __init__(self):
        self.meetingsummary_dir = MEETINGSUMMARY_DIR
        self._free_ollama_gpu_cache()
        self._check_ollama_device()

    def _free_ollama_gpu_cache(self):
        """每次启动总结前，停止 Ollama 中已加载的模型，释放 GPU 显存。

        Ollama 默认会缓存已加载的模型（占用大量 VRAM），如果不主动清理，
        后续 Pipeline 可能因显存不足而 fallback 到 CPU。

        流程：
        1. 查询当前 GPU 进程，找出 Ollama 相关进程
        2. 获取 Ollama 当前加载的模型名称
        3. 逐个 stop，并等待显存实际释放
        4. 验证显存，必要时重试
        """
        import subprocess
        import time

        # ── Step 1: 检查当前 GPU 占用 ───────────────────────────
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory,name", "--format=csv,noheader"],
            capture_output=True, text=True
        )
        ollama_procs = [
            l.strip() for l in result.stdout.strip().split("\n")
            if l.strip() and ("ollama" in l.lower() or "llama" in l.lower())
        ]
        if not ollama_procs:
            print(f"[Ollama] 当前无 Ollama GPU 进程，跳过清理", flush=True)
            return

        for line in ollama_procs:
            parts = line.split(",")
            mem_mb = int(parts[1].strip().split()[0]) if len(parts) > 1 else 0
            print(f"[Ollama] 发现已缓存模型 (占用 {mem_mb} MB 显存)，正在释放...", flush=True)

        # ── Step 2: 获取当前运行的模型名称 ──────────────────────
        result = subprocess.run(
            ["ollama", "ps", "--format", "json"],
            capture_output=True, text=True
        )
        running_models = []
        if result.returncode == 0 and result.stdout.strip():
            try:
                import json as json_mod
                ollama_data = json_mod.loads(result.stdout)
                if isinstance(ollama_data, list):
                    for proc in ollama_data:
                        model_name = proc.get("name", "")
                        if model_name:
                            running_models.append(model_name)
                elif isinstance(ollama_data, dict):
                    model_name = ollama_data.get("name", "")
                    if model_name:
                        running_models.append(model_name)
            except Exception:
                pass

        if not running_models:
            # fallback: 从 ollama ps 文本解析
            ps_result = subprocess.run(["ollama", "ps"], capture_output=True, text=True)
            lines = [l.strip() for l in ps_result.stdout.strip().split("\n") if l.strip() and l.strip().split()[0] not in ("NAME", "name")]
            for line in lines:
                model_name = line.split()[0] if line.split() else None
                if model_name and model_name not in running_models:
                    running_models.append(model_name)

        # ── Step 3: 逐个 stop，并等待显存释放 ───────────────────
        for model_name in running_models:
            print(f"[Ollama] 停止模型: {model_name}", flush=True)
            stop_result = subprocess.run(
                ["ollama", "stop", model_name],
                capture_output=True, text=True
            )
            if stop_result.returncode != 0:
                print(f"[Ollama] stop {model_name} 返回: {stop_result.stderr.strip()}", flush=True)

        # ── Step 4: 等待显存实际释放（最多等待 15 秒）──────────
        max_wait = 15
        for attempt in range(max_wait):
            time.sleep(1)
            result_check = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True
            )
            if result_check.returncode == 0:
                print(f"[Ollama] 显存状态: {result_check.stdout.strip()}", flush=True)
                # 等待显存下降到合理水平（基本只剩系统占用）
                used_mb = int(result_check.stdout.strip().split(",")[0].strip())
                if used_mb < 3000:
                    print(f"[Ollama] ✓ 显存已释放", flush=True)
                    return

        # 最终验证
        result_final = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True
        )
        print(f"[Ollama] 清理完成，当前: {result_final.stdout.strip()}", flush=True)

    def _check_ollama_device(self):
        """检查 Ollama 当前模型运行在 GPU 还是 CPU 上"""
        import subprocess
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True, text=True
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if len(lines) > 1:
            proc_line = lines[1]
            cols = " ".join(proc_line.split()).split()
            model_name = cols[0] if cols else "?"
            processor = " ".join(cols[4:6]) if len(cols) > 5 else "?"
            print(f"[Ollama] ✓ {model_name} 运行在 {processor} 上", flush=True)
        else:
            print(f"[Ollama] 当前无已加载模型（将在首次请求时加载到 GPU）", flush=True)

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
            output_dir = SUMMARIES_DIR / "period"
        output_dir.mkdir(parents=True, exist_ok=True)

        transcript_text = self._format_transcript(transcript_lines)
        result = self._call_meetingsummary(
            transcript_text,
            output_dir=output_dir,
            prefix=f"period_{meeting_id[:8]}",
            skip_eval=False,
            skip_completeness=False,
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
            output_dir = SUMMARIES_DIR / "final"
        output_dir.mkdir(parents=True, exist_ok=True)

        transcript_text = self._format_transcript(all_transcript_lines)
        result = self._call_meetingsummary(
            transcript_text,
            output_dir=output_dir,
            prefix=f"final_{meeting_id[:8]}",
            skip_eval=False,
            skip_completeness=False,
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
                    "id": str(uuid.uuid4()),
                    "content": a.get("task", a.get("description", a.get("content", ""))),
                    "assignee": a.get("assignee"),
                    "due_date": a.get("deadline", a.get("due_date")),
                    "status": "pending",
                })
            elif isinstance(a, str):
                action_items.append({"id": str(uuid.uuid4()), "content": a, "assignee": None, "due_date": None, "status": "pending"})

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

        print(f"[SummaryService] 写入转写文件...", flush=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 写入临时转写文件
        input_file = output_dir / f"{prefix}_input.txt"
        input_file.write_text(transcript_text, encoding="utf-8")

        # 确定 Python 解释器（使用 venv 的，确保依赖可用）
        import sys as _sys
        python_exe = _sys.executable

        # 构建 PYTHONPATH，确保 meetingsummary 内部导入和 backend 导入都能工作
        import os as _os
        _sep = _os.pathsep
        _env = _os.environ.copy()
        _env["PYTHONPATH"] = _sep.join([
            str(self.meetingsummary_dir),
            str(self.meetingsummary_dir.parent),
            str(self.meetingsummary_dir.parent / "backend"),
        ])

        cmd = [
            python_exe,
            str(self.meetingsummary_dir / "main.py"),
            "-i", str(input_file),
            "-o", str(output_dir),
            "--prefix", prefix,
            # 不使用 --no-map-reduce，让 Map-Reduce 自动处理长文本
            "--skip-completeness" if skip_completeness else "",
            "--skip-eval" if skip_eval else "",
            "--skip-actions" if skip_actions else "",
        ]
        cmd = [c for c in cmd if c]  # 过滤空字符串

        print(f"[SummaryService] subprocess 调用: {' '.join(cmd)}", flush=True)
        print(f"[SummaryService] PYTHONPATH={_env['PYTHONPATH']}", flush=True)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,  # 无超时限制，等待 LLM 完全生成
                cwd=str(self.meetingsummary_dir),
                env=_env,
            )
            print(f"[SummaryService] subprocess returncode={result.returncode}", flush=True)
            print(f"[SummaryService] meetingsummary stdout:\n{result.stdout}")
            if result.stderr:
                print(f"[SummaryService] meetingsummary stderr:\n{result.stderr}")
        except subprocess.TimeoutExpired:
            print(f"[SummaryService] meetingsummary 超时（600s）")
            return None
        except Exception as e:
            print(f"[SummaryService] meetingsummary 调用失败: {e}")
            return None

        # ── Ollama 调用完毕后立即释放显存 ──────────────────────────
        self._free_ollama_gpu_cache()

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

    def _call_meetingsummary_from_lines(
        self,
        transcript_lines: list[dict],
        prefix: str = "summary",
        subdir: str = "period",
    ) -> dict | None:
        """
        给定转写行列表，调用 meetingsummary CLI，返回原始 JSON 结果。
        供 asyncio.to_thread() 在线程池中同步调用，不阻塞事件循环。
        """
        output_dir = SUMMARIES_DIR / subdir
        output_dir.mkdir(parents=True, exist_ok=True)
        lines_out = []
        for line in transcript_lines:
            speaker = line.get("speaker", "未知")
            text = line.get("text", "")
            top3 = line.get("top3")
            if top3:
                top3_str = " | ".join(top3)
                lines_out.append(f"{speaker} [Top3: {top3_str}]：{text}")
            else:
                lines_out.append(f"{speaker}：{text}")
        transcript_text = "\n".join(lines_out)
        input_file = output_dir / f"{prefix}_input.txt"
        input_file.write_text(transcript_text, encoding="utf-8")

        result = self._call_meetingsummary(
            transcript_text,
            output_dir=output_dir,
            prefix=prefix,
            skip_eval=False,
            skip_completeness=False,
        )
        if result is None:
            return None

        # 提取 bullet_points
        bullet_points = []
        if result.get("tldr"):
            bullet_points.append(result["tldr"])
        for point in result.get("discussion_points", []):
            if isinstance(point, dict):
                title = point.get("title", "")
                summary = point.get("summary", "")
                bullet_points.append(f"【{title}】{summary}" if summary else title)
            elif isinstance(point, str):
                bullet_points.append(point)
        return {"bullet_points": bullet_points}
