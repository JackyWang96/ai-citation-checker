import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { uploadEssay } from '../lib/api'

const STEPS = [
  { label: '解析文档', detail: '正在提取段落与参考文献部分…', duration: 900 },
  { label: '提取引用', detail: '正在识别正文引用与参考文献条目…', duration: 700 },
  { label: '通过 Crossref 验证', detail: '正在核查参考文献…', duration: 2200 },
  { label: '运行 APA 格式校验', detail: '应用 APA 第七版格式规则…', duration: 600 },
  { label: '生成报告', detail: '交叉比对正文引用与参考文献列表…', duration: 500 },
]
const TOTAL_DURATION = STEPS.reduce((a, s) => a + s.duration, 0)

export default function Loading() {
  const location = useLocation()
  const navigate = useNavigate()
  const file: File | undefined = location.state?.file

  const [step, setStep] = useState(0)
  const [progress, setProgress] = useState(0)
  const [reportId, setReportId] = useState<string | null>(null)
  const [animDone, setAnimDone] = useState(false)

  useEffect(() => {
    if (!file) navigate('/')
  }, [file, navigate])

  // Start upload
  useEffect(() => {
    if (!file) return
    uploadEssay(file)
      .then(({ report_id }) => setReportId(report_id))
      .catch(() => navigate('/'))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Animate steps in parallel
  useEffect(() => {
    let elapsed = 0
    let stepIdx = 0
    let stepElapsed = 0

    const id = setInterval(() => {
      elapsed += 60
      stepElapsed += 60
      setProgress(Math.min(100, Math.round((elapsed / TOTAL_DURATION) * 100)))

      if (stepIdx < STEPS.length - 1 && stepElapsed >= STEPS[stepIdx].duration) {
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
            正在检查您的引用…
          </h2>
          <p style={{ color: 'var(--text-3)', fontSize: 14 }}>
            {file?.name} · 约 5 秒
          </p>
        </div>

        {/* Progress bar */}
        <div
          style={{
            background: 'var(--border)',
            borderRadius: 99,
            height: 6,
            marginBottom: 28,
            overflow: 'hidden',
          }}
        >
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
          {STEPS.map((s, i) => {
            const done = i < step
            const active = i === step
            return (
              <div
                key={s.label}
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
                    width: 20,
                    height: 20,
                    borderRadius: '50%',
                    flexShrink: 0,
                    marginTop: 2,
                    background: done ? 'var(--green)' : active ? 'var(--accent)' : 'var(--border)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'background 0.3s',
                  }}
                >
                  {done && (
                    <svg width="10" height="10" viewBox="0 0 10 10">
                      <path
                        d="M2 5l2.5 2.5L8 3"
                        stroke="white"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        fill="none"
                      />
                    </svg>
                  )}
                  {active && (
                    <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'white' }} />
                  )}
                </div>
                <div>
                  <div
                    style={{
                      fontWeight: 600,
                      fontSize: 14,
                      color: active ? 'var(--text)' : done ? 'var(--text-2)' : 'var(--text-3)',
                    }}
                  >
                    {s.label}
                  </div>
                  {active && (
                    <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 2 }}>{s.detail}</div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
