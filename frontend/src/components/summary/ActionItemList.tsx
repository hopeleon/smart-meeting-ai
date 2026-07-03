import type { ActionItem } from '../../types/meeting'

const statusMap = {
  pending: { label: '待处理', color: 'bg-yellow-100 text-yellow-800' },
  'in-progress': { label: '进行中', color: 'bg-blue-100 text-blue-800' },
  done: { label: '已完成', color: 'bg-green-100 text-green-800' },
}

export default function ActionItemList({ items }: { items: ActionItem[] }) {
  return (
    <div className="space-y-3">
      {items.map((item) => {
        const status = statusMap[item.status ?? 'pending']
        return (
          <div key={item.id} className="flex items-start gap-3 p-3 bg-gray-50 rounded-lg">
            <div className="flex-1">
              <p className="text-gray-800 text-sm">{item.content}</p>
              <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500">
                {item.assignee && <span>负责人: {item.assignee}</span>}
                {item.due_date && <span>截止: {item.due_date}</span>}
              </div>
            </div>
            <span className={`px-2 py-0.5 rounded-full text-xs ${status.color}`}>
              {status.label}
            </span>
          </div>
        )
      })}
    </div>
  )
}
