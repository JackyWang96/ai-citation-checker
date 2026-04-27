interface Issue {
  reason: string
  detail?: string
  field?: string
  expected?: string
  actual?: string
  category: string
  severity: string
  rule_id?: string
}

interface CitationData {
  id: string
  kind: string
  raw_text: string
  status: 'pass' | 'warning' | 'error'
  issues: Issue[]
}

interface Props {
  citations: CitationData[]
  activeCitationId: string | null
  onIssueClick: (id: string) => void
}

const SEVERITY_ICON: Record<string, string> = {
  error: '🔴',
  warning: '🟡',
  pass: '🟢',
}

export default function IssuePanel({ citations, activeCitationId, onIssueClick }: Props) {
  const withIssues = citations.filter(c => c.status !== 'pass')

  if (withIssues.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400 text-sm">
        🎉 No issues found!
      </div>
    )
  }

  return (
    <div className="divide-y divide-gray-100">
      {withIssues.map(c => (
        <div
          key={c.id}
          className={`p-4 cursor-pointer hover:bg-gray-50 transition
            ${activeCitationId === c.id ? 'bg-blue-50' : ''}
          `}
          onClick={() => onIssueClick(c.id)}
        >
          <div className="flex items-start gap-2">
            <span>{SEVERITY_ICON[c.status]}</span>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-mono text-gray-500 truncate">{c.raw_text}</p>
              {c.issues.map((issue, i) => (
                <div key={i} className="mt-1">
                  <p className="text-sm text-gray-800">{issue.reason}</p>
                  {issue.expected && (
                    <p className="text-xs text-gray-500 mt-0.5">
                      Expected: <span className="text-green-700">{issue.expected}</span>
                      {issue.actual && <> · Got: <span className="text-red-600">{issue.actual}</span></>}
                    </p>
                  )}
                  {issue.detail && (
                    <p className="text-xs text-gray-400 mt-0.5">{issue.detail}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
