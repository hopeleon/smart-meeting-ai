import type { PeriodSummary } from '../../types/meeting'

export default function PeriodSummaryCard({ summary }: { summary: PeriodSummary }) {
  const minutes = Math.floor(summary.period_end / 60)
  const seconds = Math.floor(summary.period_end % 60)
  const timeLabel = `${minutes}:${seconds.toString().padStart(2, '0')}`

  return (
    <div className="bg-dark-100 rounded-lg p-4 border border-dark-200">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs font-mono text-primary bg-primary/10 px-2 py-0.5 rounded">
          {timeLabel}
        </span>
        <span className="text-xs text-gray-500">阶段总结</span>
      </div>
      <ul className="space-y-2">
        {summary.bullet_points.map((point, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-gray-200">
            <span className="text-primary mt-0.5">•</span>
            <span>{point}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
