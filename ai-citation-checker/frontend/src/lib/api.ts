const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export async function uploadEssay(file: File): Promise<{ report_id: string; url: string }> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/api/check`, { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail ?? `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchReport(id: string) {
  const res = await fetch(`${BASE}/api/report/${id}`)
  if (res.status === 410) throw new Error('Report expired or not found')
  if (!res.ok) throw new Error(`Failed to load report: ${res.status}`)
  return res.json()
}
