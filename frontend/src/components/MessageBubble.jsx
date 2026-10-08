// Lightweight rich-text rendering (no markdown library needed):
// [text](url) links, **bold**, `code`, and bare URLs become real elements.
const RICH_RE =
  /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(\*\*([^*]+)\*\*)|(`([^`]+)`)|(https?:\/\/[^\s<>()"]+)/g

function renderRich(text) {
  if (!text) return text
  const nodes = []
  let last = 0
  let key = 0
  RICH_RE.lastIndex = 0
  let m
  while ((m = RICH_RE.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    if (m[1] && m[2]) {
      nodes.push(
        <a key={key++} href={m[2]} target="_blank" rel="noreferrer">
          {m[1]}
        </a>
      )
    } else if (m[4]) {
      nodes.push(<strong key={key++}>{m[4]}</strong>)
    } else if (m[6]) {
      nodes.push(<code key={key++}>{m[6]}</code>)
    } else if (m[7]) {
      nodes.push(
        <a key={key++} href={m[7]} target="_blank" rel="noreferrer">
          {m[7]}
        </a>
      )
    }
    last = m.index + m[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

export default function MessageBubble({ message }) {
  const { role, content, toolCalls, memoriesUsed, isError, streaming } = message

  if (role === 'system') {
    return (
      <div className="row system">
        <div className="sysnote">{content}</div>
      </div>
    )
  }

  return (
    <div className={`row ${role}`}>
      <div className={`bubble ${role} ${isError ? 'error' : ''}`}>
        <div className="content">
          {renderRich(content)}
          {streaming && <span className="cursor">▍</span>}
        </div>

        {toolCalls?.length > 0 && (
          <div className="meta">
            {toolCalls.map((tc, i) => (
              <span
                key={i}
                className={`chip ${tc.running ? 'chip-running' : tc.success ? '' : 'chip-failed'}`}
                title={`args: ${JSON.stringify(tc.arguments)}${tc.result_preview ? `\nresult: ${tc.result_preview}` : ''}`}
              >
                {tc.running ? <span className="spinner" /> : '🔧'} {tc.tool}
                {tc.running ? ' · running…' : ` · ${tc.duration_ms}ms${tc.success ? '' : ' · failed'}`}
              </span>
            ))}
          </div>
        )}

        {memoriesUsed?.length > 0 && (
          <div className="meta">
            <span className="chip chip-memory" title={memoriesUsed.join('\n')}>
              🧠 {memoriesUsed.length} memor{memoriesUsed.length === 1 ? 'y' : 'ies'} recalled
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
