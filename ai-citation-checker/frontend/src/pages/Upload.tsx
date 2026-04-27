import { useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

export function LogoIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="3" width="18" height="18" rx="4" fill="var(--accent)" opacity="0.15" />
      <path d="M7 8h10M7 12h7M7 16h5" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="18" cy="16" r="3.5" fill="var(--amber-bg)" stroke="var(--amber)" strokeWidth="1.5" />
      <path d="M16.8 16h2.4M18 14.8v2.4" stroke="var(--amber)" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  )
}

export default function Upload() {
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(
    (file: File) => {
      if (!file.name.match(/\.(docx|pdf)$/i)) {
        setError('仅支持 .docx 和 .pdf 文件')
        return
      }
      setError('')
      navigate('/loading', { state: { file } })
    },
    [navigate],
  )

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      const file = e.dataTransfer.files[0]
      if (file) handleFile(file)
    },
    [handleFile],
  )

  const onInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
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
      {/* Navbar */}
      <nav
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          height: 56,
          padding: '0 32px',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          borderBottom: '1px solid var(--border)',
          background: 'rgba(255,255,255,0.85)',
          backdropFilter: 'blur(12px)',
          zIndex: 50,
        }}
      >
        <LogoIcon />
        <span style={{ fontWeight: 700, fontSize: 15, letterSpacing: '-0.01em' }}>引文检查器</span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 500,
            padding: '2px 8px',
            background: 'var(--accent-bg)',
            color: 'var(--accent)',
            borderRadius: 99,
          }}
        >
          APA 7th
        </span>
      </nav>

      {/* Hero */}
      <div style={{ textAlign: 'center', maxWidth: 560, marginBottom: 48, marginTop: 56 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--accent)',
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            marginBottom: 16,
          }}
        >
          AI 驱动 · APA 第七版
        </div>
        <h1
          style={{
            fontSize: 42,
            fontWeight: 700,
            lineHeight: 1.15,
            letterSpacing: '-0.03em',
            marginBottom: 16,
            color: 'var(--text)',
          }}
        >
          提交前，验证每一条引用。
        </h1>
        <p style={{ color: 'var(--text-2)', fontSize: 16, lineHeight: 1.7 }}>
          上传您的论文，在约 5 秒内收到彩色标注的引用核查报告——检测虚假引用、元数据错误和 APA 格式违规。
        </p>
      </div>

      {/* Drop zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        style={{
          width: '100%',
          maxWidth: 520,
          border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border)'}`,
          borderRadius: 16,
          padding: '52px 32px',
          textAlign: 'center',
          cursor: 'pointer',
          background: dragging ? 'var(--accent-bg)' : 'var(--surface)',
          transition: 'all 0.18s ease',
          boxShadow: dragging ? '0 0 0 4px oklch(52% 0.16 240 / 0.12)' : 'none',
        }}
      >
        <input ref={inputRef} type="file" accept=".docx,.pdf" style={{ display: 'none' }} onChange={onInput} />
        <div style={{ marginBottom: 16 }}>
          <svg
            width="48"
            height="48"
            viewBox="0 0 48 48"
            fill="none"
            style={{ opacity: dragging ? 1 : 0.6, margin: '0 auto', display: 'block' }}
          >
            <rect
              x="8"
              y="6"
              width="32"
              height="36"
              rx="4"
              fill={dragging ? 'var(--accent-bg)' : 'var(--bg)'}
              stroke="var(--border)"
              strokeWidth="2"
            />
            <path d="M28 6v8h8" stroke="var(--border)" strokeWidth="2" strokeLinejoin="round" />
            <path
              d="M16 26l8-8 8 8M24 18v14"
              stroke="var(--accent)"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <p style={{ fontWeight: 600, fontSize: 16, marginBottom: 6 }}>
          {dragging ? '将文件拖放至此' : '拖拽上传论文'}
        </p>
        <p style={{ color: 'var(--text-3)', fontSize: 13 }}>
          或{' '}
          <span style={{ color: 'var(--accent)', fontWeight: 600 }}>点击选择文件</span>
          {' '}— .docx 或 .pdf 文件
        </p>
      </div>

      {error && (
        <p style={{ marginTop: 12, fontSize: 13, color: 'var(--red)' }}>{error}</p>
      )}

      {/* Feature pills */}
      <div
        style={{ display: 'flex', gap: 10, marginTop: 32, flexWrap: 'wrap', justifyContent: 'center' }}
      >
        {[
          { icon: '🔴', label: '虚假引用检测' },
          { icon: '🟡', label: '元数据不一致' },
          { icon: '🟡', label: 'APA 格式违规' },
          { icon: '🟢', label: '引用验证通过' },
        ].map((f) => (
          <div
            key={f.label}
            style={{
              padding: '6px 14px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 99,
              fontSize: 13,
              color: 'var(--text-2)',
            }}
          >
            {f.icon} {f.label}
          </div>
        ))}
      </div>

      <p style={{ marginTop: 24, fontSize: 12, color: 'var(--text-3)' }}>
        无需登录 · 报告在 24 小时后自动删除 · 文件不会被存储
      </p>
    </div>
  )
}
