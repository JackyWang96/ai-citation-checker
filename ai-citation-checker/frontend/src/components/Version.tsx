/**
 * Tiny build-version badge — fixed bottom-right, monospace, low contrast.
 * Helps confirm you're running the latest code after hot-reload / rebuild.
 */
export function Version() {
  const date = new Date(__BUILD_TIME__)
  const local = date.toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
  return (
    <div
      style={{
        position: 'fixed',
        bottom: 8,
        right: 10,
        fontFamily: 'JetBrains Mono, ui-monospace, monospace',
        fontSize: 11,
        color: 'rgba(0,0,0,0.4)',
        pointerEvents: 'none',
        userSelect: 'none',
        zIndex: 9999,
      }}
      title={`Built ${__BUILD_TIME__} · ${__GIT_SHA__}`}
    >
      v{local} · {__GIT_SHA__}
    </div>
  )
}
