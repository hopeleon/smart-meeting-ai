import type { TranscriptSegment } from '../../types/meeting'

const speakerColors: Record<string, string> = {
  Speaker_1: 'border-blue-500',
  Speaker_2: 'border-green-500',
  Speaker_3: 'border-purple-500',
  Speaker_4: 'border-orange-500',
}

export default function TranscriptItem({ item }: { item: TranscriptSegment }) {
  const borderColor = speakerColors[item.speaker_id] || 'border-gray-500'

  return (
    <div className={`border-l-2 ${borderColor} pl-3 py-1.5`}>
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-xs font-medium text-primary">{item.speaker_label}</span>
        <span className="text-xs text-gray-500">
          {item.start_time.toFixed(1)}s - {item.end_time.toFixed(1)}s
        </span>
      </div>
      <p className="text-sm text-gray-200">{item.text}</p>
    </div>
  )
}
