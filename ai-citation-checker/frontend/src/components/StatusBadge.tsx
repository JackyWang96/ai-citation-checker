type Status = 'pass' | 'warning' | 'error'

const MAP: Record<Status, { label: string; color: string; bg: string }> = {
  pass:    { label: 'Pass',    color: 'var(--green)', bg: 'var(--green-bg)' },
  warning: { label: 'Warning', color: 'var(--amber)', bg: 'var(--amber-bg)' },
  error:   { label: 'Error',   color: 'var(--red)',   bg: 'var(--red-bg)'   },
}

export default function StatusBadge({ status }: { status: Status }) {
  const s = MAP[status]
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 8px',
        borderRadius: 99,
        background: s.bg,
        color: s.color,
        fontSize: 11,
        fontWeight: 600,
        fontFamily: 'var(--mono)',
        whiteSpace: 'nowrap',
      }}
    >
      ● {s.label}
    </span>
  )
}
