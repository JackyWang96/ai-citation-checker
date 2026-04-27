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
  isActive: boolean
  isHovered: boolean
  onClick: (id: string) => void
  onHover: (id: string | null) => void
}

const COLOR_MAP = {
  pass:    { border: 'var(--green)', bg: 'var(--green-bg)' },
  warning: { border: 'var(--amber)', bg: 'var(--amber-bg)' },
  error:   { border: 'var(--red)',   bg: 'var(--red-bg)'   },
}

export default function Citation({ id, status, text, issues, isActive, isHovered, onClick, onHover }: Props) {
  const [showTip, setShowTip] = useState(false)
  const c = COLOR_MAP[status]
  const highlighted = isActive || isHovered
  const first = issues[0]

  return (
    <span style={{ position: 'relative', display: 'inline' }}>
      <span
        id={id}
        data-cit-id={id}
        onClick={() => onClick(id)}
        onMouseEnter={() => { setShowTip(true); onHover(id) }}
        onMouseLeave={() => { setShowTip(false); onHover(null) }}
        style={{
          background: highlighted ? c.bg : 'transparent',
          borderBottom: `2px solid ${c.border}`,
          borderRadius: highlighted ? 3 : 0,
          padding: highlighted ? '1px 3px' : '0 1px',
          cursor: 'pointer',
          transition: 'all 0.15s ease',
          outline: isActive ? `2px solid ${c.border}` : 'none',
          outlineOffset: 1,
          fontWeight: isActive ? 600 : 'inherit',
        }}
      >
        {text}
      </span>
      {showTip && first && (
        <span
          style={{
            position: 'absolute',
            bottom: 'calc(100% + 6px)',
            left: '50%',
            transform: 'translateX(-50%)',
            background: status === 'error' ? 'var(--red)' : 'var(--text)',
            color: 'white',
            padding: '6px 10px',
            borderRadius: 6,
            fontSize: 12,
            whiteSpace: 'normal',
            maxWidth: 260,
            lineHeight: 1.4,
            pointerEvents: 'none',
            zIndex: 100,
            boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
          }}
        >
          {first.reason}
        </span>
      )}
    </span>
  )
}
