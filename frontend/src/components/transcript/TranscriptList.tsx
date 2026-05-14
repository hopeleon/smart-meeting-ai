import { useEffect, useRef } from 'react'
import type { TranscriptSegment } from '../../types/meeting'
import TranscriptItem from './TranscriptItem'

interface Props {
  items: TranscriptSegment[]
}

export default function TranscriptList({ items }: Props) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [items])

  if (items.length === 0) {
    return (
      <div className="text-center text-gray-500 py-12 text-sm">
        等待转写结果...
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {items.map((item) => (
        <TranscriptItem key={item.id} item={item} />
      ))}
      <div ref={endRef} />
    </div>
  )
}
