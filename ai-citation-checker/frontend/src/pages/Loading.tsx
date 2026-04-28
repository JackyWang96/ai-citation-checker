import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { uploadEssay } from '../lib/api'
import { useT } from '../i18n'

const STEP_DURATIONS = [900, 700, 2200, 600, 500]
const TOTAL_DURATION = STEP_DURATIONS.reduce((a, d) => a + d, 0)

export default function Loading() {
  const { t } = useT()
  const location = useLocation()
  const navigate = useNavigate()
  const file: File | undefined = location.state?.file

  const [step, setStep] = useState(0)
  const [progress, setProgress] = useState(0)
  const [uploadError, setUploadError] = useState('')
  const [reportId, setReportId] = useState<string | null>(null)
  const [animDone, setAnimDone] = useState(false)

  // Redirect only when there's no file passed (direct URL access)
  useEffect(() => {
    if (!file) navigate('/')
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Start upload — show error state on failure, don't silently redirect
  useEffect(() => {
    if (!file) return
    uploadEssay(file)
      .then(({ report_id }) => setReportId(report_id))
      .catch((e: unknown) => {
        setUploadError(e instanceof Error ? e.message : t.loadingError)
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Animate steps in parallel with upload
  useEffect(() => {
    let elapsed = 0
    let stepIdx = 0
    let stepElapsed = 0

    const id = setInterval(() => {
      elapsed += 60
      stepElapsed += 60
      setProgress(Math.min(100, Math.round((elapsed / TOTAL_DURATION) * 100)))

      if (stepIdx < STEP_DURATIONS.length - 1 && stepElapsed >= STEP_DURATIONS[stepIdx]) {
        stepIdx++
        stepElapsed = 0
        setStep(stepIdx)
      }

      if (elapsed >= TOTAL_DURATION) {
        clearInterval(id)
        setAnimDone(true)
      }
    }, 60)

    return () => clearInterval(id)
  }, [])

  // Navigate when both animation and upload are done
  useEffect(() => {
    if (animDone && reportId) {
      navigate(`/r/${reportId}`)
    }
  }, [animDone, reportId, navigate])

  // Error state
  if (uploadError) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 32,
          background: 'var(--bg)',
        }}
      >
        <div style={{ textAlign: 'center', maxWidth: 400 }}>
          <div style={{ fontSize: 40, marginBottom: 16 }}>⚠️</div>
          <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 8, color: 'var(--text)' }}>
            {t.loadingError}
          </h2>
          <p style={{ fontSize: 13, color: 'var(--text-3)', marginBottom: 24, fontFamily: 'var(--mono)' }}>
            {uploadError}
          </p>
          <button
            onClick={() => navigate('/')}
            style={{
              padding: '10px 24px',
              background: 'var(--accent)',
              color: 'white',
              borderRadius: 8,
              fontSize: 14,
              fontWeight: 600,
              cursor: 'pointer',
              fontFamily: 'var(--font)',
            }}
          >
            {t.backToUpload}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 32,
        background: 'var(--bg)',
      }}
    >
      <div style={{ width: '100%', maxWidth: 440 }}>
        {/* Spinner + heading */}
        <div style={{ marginBottom: 32, textAlign: 'center' }}>
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: '50%',
              border: '3px solid var(--border)',
              borderTopColor: 'var(--accent)',
              margin: '0 auto 20px',
              animation: 'spin 0.9s linear infinite',
            }}
          />
          <h2 style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em', marginBottom: 6 }}>
            {t.loadingTitle}
          </h2>
          <p style={{ color: 'var(--text-3)', fontSize: 14 }}>
            {file?.name} · {t.loadingEta}
          </p>
        </div>

        {/* Progress bar */}
        <div style={{ background: 'var(--border)', borderRadius: 99, height: 6, marginBottom: 28, overflow: 'hidden' }}>
          <div
            style={{
              height: '100%',
              background: 'var(--accent)',
              borderRadius: 99,
              width: `${progress}%`,
              transition: 'width 0.15s ease',
            }}
          />
        </div>

        {/* Steps */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {t.loadingSteps.map((s, i) => {
            const done = i < step
            const active = i === step
            return (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 12,
                  opacity: done || active ? 1 : 0.35,
                  transition: 'opacity 0.3s',
                }}
              >
                <div
                  style={{
                    width: 20, height: 20, borderRadius: '50%', flexShrink: 0, marginTop: 2,
                    background: done ? 'var(--green)' : active ? 'var(--accent)' : 'var(--border)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'background 0.3s',
                  }}
                >
                  {done && (
                    <svg width="10" height="10" viewBox="0 0 10 10">
                      <path d="M2 5l2.5 2.5L8 3" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                    </svg>
                  )}
                  {active && <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'white' }} />}
                </div>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 14, color: active ? 'var(--text)' : done ? 'var(--text-2)' : 'var(--text-3)' }}>
                    {s.label}
                  </div>
                  {active && <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 2 }}>{s.detail}</div>}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
