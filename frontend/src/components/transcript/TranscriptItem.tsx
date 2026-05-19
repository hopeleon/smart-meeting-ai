import type { TranscriptSegment } from '../../types/meeting'

const speakerColors: Record<string, string> = {
  Speaker_1: 'border-blue-500',
  Speaker_2: 'border-green-500',
  Speaker_3: 'border-purple-500',
  Speaker_4: 'border-orange-500',
}

function getDisplayName(item: TranscriptSegment): string {
  // 优先级：speaker_name（真实姓名） > speaker_label（speaker_id） > "未知"
  if (item.speaker_name) return item.speaker_name
  if (item.speaker_label) return item.speaker_label
  if (item.speaker_id && item.speaker_id !== 'unknown' && item.speaker_id !== 'speaker_unk') return item.speaker_id
  return '未知'
}

export default function TranscriptItem({ item }: { item: TranscriptSegment }) {
  const borderColor = speakerColors[item.speaker_id] || 'border-gray-500'
  const displayName = getDisplayName(item)
  const startMs = item.start_ms ?? (item.start_time != null ? item.start_time * 1000 : undefined)
  const endMs = item.end_ms ?? (item.end_time != null ? item.end_time * 1000 : undefined)

  return (
    <div className={`border-l-2 ${borderColor} pl-3 py-1.5`}>
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-xs font-medium text-primary">{displayName}</span>
        {startMs != null && endMs != null ? (
          <span className="text-xs text-gray-500">
            {(startMs / 1000).toFixed(1)}s - {(endMs / 1000).toFixed(1)}s
          </span>
        ) : null}
      </div>
      <p className="text-sm text-gray-200">{item.text}</p>
    </div>
  )
}
