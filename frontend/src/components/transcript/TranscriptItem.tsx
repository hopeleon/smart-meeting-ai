import type { TranscriptSegment } from '../../types/meeting'

const speakerColors: Record<string, string> = {
  speaker_1: 'border-blue-500',
  speaker_2: 'border-green-500',
  speaker_3: 'border-purple-500',
  speaker_4: 'border-orange-500',
}

function getDisplayName(item: TranscriptSegment): string {
  // 优先级：speaker_name（真实姓名） > speaker_label（speaker_id） > "未知"
  if (item.speaker_name) return item.speaker_name
  if (item.speaker_label) return item.speaker_label
  if (item.speaker_id && item.speaker_id !== 'unknown' && item.speaker_id !== 'speaker_unk') return item.speaker_id
  return '未知'
}

export default function TranscriptItem({ item }: { item: TranscriptSegment }) {
  const borderColor = speakerColors[(item.speaker_id || '').toLowerCase()] || 'border-slate-400'
  const displayName = getDisplayName(item)
  const startMs = item.start_ms ?? (item.start_time != null ? item.start_time * 1000 : undefined)
  const endMs = item.end_ms ?? (item.end_time != null ? item.end_time * 1000 : undefined)

  return (
    <div className={`border-l-2 ${borderColor} pl-3 py-1.5`}>
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-xs font-semibold text-sky-700">{displayName}</span>
        {item.identified ? (
          <span
            className="text-[10px] px-1 py-0.5 rounded bg-green-600/20 text-green-400"
            title="声纹已识别"
          >
            ✓ 已识别
            {(item.speaker_confidence ?? item.confidence) != null
              ? ` ${Math.round(((item.speaker_confidence ?? item.confidence) as number) * 100)}%`
              : ''}
          </span>
        ) : null}
        {!item.identified && item.best_guess_name ? (
          <span
            className="text-[10px] px-1 py-0.5 rounded bg-gray-600/20 text-gray-400"
            title="声纹猜测（未达确信阈值，仅供参考）"
          >
            可能 {item.best_guess_name}
            {item.best_guess_score != null ? ` ${Math.round(item.best_guess_score * 100)}%` : ''}
          </span>
        ) : null}
        {startMs != null && endMs != null ? (
          <span className="text-xs text-slate-500">
            {(startMs / 1000).toFixed(1)}s - {(endMs / 1000).toFixed(1)}s
          </span>
        ) : null}
      </div>
      <p className="text-sm leading-6 text-slate-700">{item.text}</p>
    </div>
  )
}
