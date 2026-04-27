import { useState } from 'react'

interface Issue {
  reason: string
  detail?: string
  field?: string
  expected?: string
  actual?: string
  rule_id?: string
  category: string
  severity: string
}

interface Props {
  id: string
  status: 'pass' | 'warning' | 'error'
  text: string
  issues: Issue[]
  onClick: (id: string) => void
  highlighted: boolean
}

const COLOR: Record<string, string> = {
  pass: 'bg-green-100 text-green-800',
  warning: 'bg-yellow-100 text-yellow-800',
  error: 'bg-red-100 text-red-800',
}

export default function Citation({ id, status, text, issues, onClick, highlighted }: Props) {
  const [showTip, setShowTip] = useState(false)
  const first = issues[0]

  return (
    <span
      id={id}
      className={`relative cursor-pointer px-0.5 rounded transition
        ${COLOR[status]}
        ${highlighted ? 'ring-2 ring-blue-500' : ''}
      `}
      onClick={() => onClick(id)}
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      {text}
      {showTip && first && (
        <span className="absolute bottom-full left-0 mb-1 z-10 w-64 bg-gray-800 text-white text-xs rounded p-2 shadow-lg">
          {first.reason}
        </span>
      )}
    </span>
  )
}
