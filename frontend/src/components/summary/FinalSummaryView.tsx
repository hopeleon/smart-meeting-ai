import type { FinalSummary } from '../../types/meeting'

export default function FinalSummaryView({ summary }: { summary: FinalSummary }) {
  return (
    <div>
      <h2 className="text-xl font-bold mb-4">会议总结</h2>

      {/* 概述 */}
      <div className="mb-6">
        <p className="text-gray-700 leading-relaxed">{summary.overview}</p>
      </div>

      {/* 关键决策 */}
      {(summary.key_decisions?.length ?? 0) > 0 && (
        <div className="mb-6">
          <h3 className="text-lg font-semibold mb-3">关键决策</h3>
          <ul className="space-y-2">
            {summary.key_decisions!.map((decision, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="text-blue-600 font-bold mt-0.5">{i + 1}.</span>
                <span className="text-gray-700">{decision}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
