import { useState } from 'react'
import { useT } from '../i18n'

/**
 * Copy-current-URL-to-clipboard button. Briefly swaps to "Copied!" on success.
 */
export default function CopyLinkButton() {
  const { t } = useT()
  const [copied, setCopied] = useState(false)

  const handleClick = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      // Clipboard API may be unavailable (e.g. insecure context) — silently ignore
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={copied}
      style={{
        padding: '6px 12px',
        borderRadius: 6,
        fontSize: 12,
        fontWeight: 600,
        color: copied ? 'var(--green)' : 'var(--accent)',
        background: copied ? 'var(--green-bg)' : 'transparent',
        border: `1px solid ${copied ? 'var(--green-border)' : 'var(--border)'}`,
        cursor: copied ? 'default' : 'pointer',
        transition: 'all 0.15s ease',
      }}
    >
      {copied ? t.copied : t.copyLink}
    </button>
  )
}
