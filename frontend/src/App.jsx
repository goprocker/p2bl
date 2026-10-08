import { useEffect, useRef, useState } from 'react'
import AutomationLab from './components/AutomationLab.jsx'
import ChatBox from './components/ChatBox.jsx'

const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8001'
const USER_ID = 'default_user'


export default function App() {
  const [view, setView] = useState('automation')
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content:
        "Hi, I'm P2BL — your rule-based smart-home assistant. Ask how the home handles lighting, energy saving, appliance control, comfort, or safety.",
    },
  ])
  const [conversationId, setConversationId] = useState(null)
  const [loading, setLoading] = useState(false)
  const [speakReplies, setSpeakReplies] = useState(false)
  const [speaking, setSpeaking] = useState(false) // hands-free pauses while true
  const [health, setHealth] = useState(null)
  const [conversations, setConversations] = useState([])
  const [models, setModels] = useState([])
  const [model, setModel] = useState(
    // read the pre-rename key too so the saved choice survives
    localStorage.getItem('p2bl-model') || localStorage.getItem('jarvis-model') || ''
  )
  const audioRef = useRef(null)
  const abortRef = useRef(null) // AbortController of the in-flight request
  // Stream callbacks fire long after render — a ref always reads the current toggle
  const speakRepliesRef = useRef(speakReplies)
  speakRepliesRef.current = speakReplies

  function stopGeneration() {
    // Aborts the fetch → the SSE connection drops → the backend cancels the
    // agent loop → Ollama stops generating. Also silences a playing reply.
    abortRef.current?.abort()
    if (audioRef.current) {
      audioRef.current.pause()
      setSpeaking(false)
    }
  }

  // Poll backend health for the header status light
  useEffect(() => {
    if (view !== 'chat') return undefined
    let cancelled = false
    async function check() {
      try {
        const res = await fetch(`${API_BASE}/health`)
        const data = await res.json()
        if (!cancelled) setHealth(data)
      } catch {
        if (!cancelled) setHealth({ status: 'down' })
      }
    }
    check()
    const id = setInterval(check, 30_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [view])

  function playBase64Audio(b64, mime = 'audio/wav') {
    // Stop any previous reply that is still talking
    if (audioRef.current) audioRef.current.pause()
    const audio = new Audio(`data:${mime};base64,${b64}`)
    audioRef.current = audio
    setSpeaking(true)
    audio.onended = () => setSpeaking(false)
    audio.onerror = () => setSpeaking(false)
    audio.play().catch(() => setSpeaking(false)) // autoplay is allowed after a user gesture
  }

  async function speak(text) {
    try {
      const res = await fetch(`${API_BASE}/voice/speak`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      if (!res.ok) return
      const data = await res.json()
      playBase64Audio(data.audio_base64, data.audio_mime)
    } catch {
      /* speaking is best-effort — the text answer is already on screen */
    }
  }

  // Mutate the last (streaming) assistant bubble as events arrive
  function updateLast(updater) {
    setMessages((prev) => {
      const next = [...prev]
      next[next.length - 1] = updater(next[next.length - 1])
      return next
    })
  }

  function handleStreamEvent(event) {
    if (event.type === 'token') {
      updateLast((m) => ({ ...m, content: m.content + event.text }))
    } else if (event.type === 'reset') {
      updateLast((m) => ({ ...m, content: '' }))
    } else if (event.type === 'tool_start') {
      updateLast((m) => ({
        ...m,
        toolCalls: [...(m.toolCalls || []), { tool: event.tool, arguments: event.arguments, running: true }],
      }))
    } else if (event.type === 'tool_end') {
      updateLast((m) => {
        const toolCalls = [...(m.toolCalls || [])]
        const i = toolCalls.findIndex((t) => t.running && t.tool === event.tool)
        const record = {
          tool: event.tool,
          arguments: event.arguments,
          result_preview: event.result_preview,
          success: event.success,
          duration_ms: event.duration_ms,
        }
        if (i >= 0) toolCalls[i] = record
        else toolCalls.push(record)
        return { ...m, toolCalls }
      })
    } else if (event.type === 'done') {
      setConversationId(event.conversation_id)
      updateLast((m) => ({
        ...m,
        content: event.answer,
        toolCalls: event.tool_calls,
        memoriesUsed: event.memories_used,
        streaming: false,
      }))
      if (speakRepliesRef.current) speak(event.answer)
    } else if (event.type === 'error') {
      updateLast((m) => ({ ...m, content: `⚠️ ${event.detail}`, isError: true, streaming: false }))
    }
  }

  async function sendMessage(text) {
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text },
      // Placeholder bubble that fills up live as tokens stream in
      { role: 'assistant', content: '', streaming: true, toolCalls: [] },
    ])
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          message: text,
          user_id: USER_ID,
          conversation_id: conversationId,
          model: model || undefined,
        }),
      })
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `Backend returned ${res.status}`)
      }

      // Parse the SSE stream: events are "data: {json}\n\n" blocks
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let finished = false
      while (!finished) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() // keep any incomplete block for the next read
        for (const block of blocks) {
          const line = block.trim()
          if (!line.startsWith('data:')) continue
          const event = JSON.parse(line.slice(5))
          handleStreamEvent(event)
          if (event.type === 'done' || event.type === 'error') finished = true
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        // User hit Stop — keep whatever streamed in, mark it as cut short
        updateLast((m) => ({
          ...m,
          content: m.content ? `${m.content} …` : '⏹ Stopped before answering.',
          stopped: true,
          streaming: false,
        }))
      } else {
        updateLast((m) => ({ ...m, content: `⚠️ ${err.message}`, isError: true, streaming: false }))
      }
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }

  async function sendVoice(audioBlob) {
    // Placeholder bubble while we transcribe + think
    setMessages((prev) => [...prev, { role: 'user', content: '🎤 …', pending: true }])
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const form = new FormData()
      const filename = audioBlob.type.includes('wav') ? 'recording.wav' : 'recording.webm'
      form.append('audio', audioBlob, filename)
      form.append('user_id', USER_ID)
      if (conversationId) form.append('conversation_id', conversationId)
      if (model) form.append('model', model)

      const res = await fetch(`${API_BASE}/voice/chat`, {
        method: 'POST',
        body: form,
        signal: controller.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `Backend returned ${res.status}`)
      }
      const data = await res.json()
      setConversationId(data.conversation_id)
      setMessages((prev) => [
        // Replace the 🎤 placeholder with what Whisper actually heard
        ...prev.slice(0, -1),
        { role: 'user', content: `🎤 ${data.transcript}` },
        {
          role: 'assistant',
          content: data.answer,
          toolCalls: data.tool_calls,
          memoriesUsed: data.memories_used,
        },
      ])
      playBase64Audio(data.audio_base64, data.audio_mime)
    } catch (err) {
      setMessages((prev) => [
        ...prev.slice(0, -1),
        err.name === 'AbortError'
          ? { role: 'assistant', content: '⏹ Stopped.', stopped: true }
          : { role: 'assistant', content: `⚠️ ${err.message}`, isError: true },
      ])
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }

  function newConversation() {
    setConversationId(null)
    setMessages([{ role: 'assistant', content: 'New conversation started. What can I do for you?' }])
  }

  // Model picker: load what the Ollama host has installed
  useEffect(() => {
    if (view !== 'chat') return undefined
    let cancelled = false
    fetch(`${API_BASE}/models`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data) return
        setModels(data.models)
        // First visit (or saved model no longer installed) → use the default
        setModel((current) =>
          current && data.models.some((m) => m.name === current) ? current : data.default
        )
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [view])

  function chooseModel(name) {
    setModel(name)
    localStorage.setItem('p2bl-model', name)
    // Client-side note only — not sent to the backend or saved in Mongo
    setMessages((prev) => [...prev, { role: 'system', content: `Model switched to ${name}` }])
  }

  // Sidebar list refreshes on mount and after every completed exchange
  useEffect(() => {
    if (view !== 'chat' || loading) return undefined
    let cancelled = false
    fetch(`${API_BASE}/conversations?user_id=${USER_ID}`)
      .then((res) => (res.ok ? res.json() : []))
      .then((list) => {
        if (!cancelled) setConversations(list)
      })
      .catch(() => {}) // sidebar is best-effort
    return () => {
      cancelled = true
    }
  }, [loading, view])

  async function openConversation(conv) {
    try {
      const res = await fetch(`${API_BASE}/conversations/${conv.id}/messages`)
      if (!res.ok) throw new Error(`Backend returned ${res.status}`)
      const history = await res.json()
      setConversationId(conv.id)
      setMessages(
        history.length
          ? history.map((m) => ({ role: m.role, content: m.content }))
          : [{ role: 'assistant', content: 'This conversation is empty — say something!' }]
      )
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `⚠️ Could not load that conversation: ${err.message}`, isError: true },
      ])
    }
  }

  function historyLabel(conv) {
    const d = new Date(conv.last_message_at)
    const today = new Date()
    const sameDay = d.toDateString() === today.toDateString()
    return sameDay
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString([], { day: 'numeric', month: 'short' })
  }

  const healthy = view === 'automation' || health?.status === 'ok'
  const statusText =
    view === 'automation'
      ? 'mock engine online'
      : health == null
      ? 'checking…'
      : healthy
        ? `online · ${model || health.model}`
        : health.status === 'degraded'
          ? `degraded · mongo ${health.mongo} · ${health.llm_provider || 'ollama'} ${health.llm || health.ollama}`
          : 'backend offline'

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-brand">P2BL</div>
        <div className="mode-switch">
          <button className={view === 'automation' ? 'active' : ''}
            onClick={() => setView('automation')}>Automation Lab</button>
          <button className={view === 'chat' ? 'active' : ''}
            onClick={() => setView('chat')}>AI Chat</button>
        </div>
        <button className="new-chat-btn" onClick={newConversation}>
          + New chat
        </button>
        <div className="history-list">
          {conversations.length === 0 && <div className="history-empty">No conversations yet</div>}
          {conversations.map((conv) => (
            <button
              key={conv.id}
              className={`history-item ${conv.id === conversationId ? 'active' : ''}`}
              onClick={() => openConversation(conv)}
              title={`${conv.message_count} messages`}
            >
              <span className="history-item-title">{conv.title}</span>
              <span className="history-item-date">{historyLabel(conv)}</span>
            </button>
          ))}
        </div>
        <div className="sidebar-footer">
          <span className={`statusline ${healthy ? 'ok' : 'bad'}`}>
            <span className="status-dot" />
            {statusText}
          </span>
          <button
            className={`ghost-btn ${speakReplies ? 'active' : ''}`}
            onClick={() => setSpeakReplies((v) => !v)}
            title="Also speak answers to typed messages"
          >
            {speakReplies ? '🔊 voice on' : '🔇 voice off'}
          </button>
        </div>
      </aside>
      <main className={`chat-main ${view === 'automation' ? 'automation-main' : ''}`}>
        <div className="mobile-view-switch">
          <button className={view === 'automation' ? 'active' : ''}
            onClick={() => setView('automation')}>Lab</button>
          <button className={view === 'chat' ? 'active' : ''}
            onClick={() => setView('chat')}>Chat</button>
        </div>
        {view === 'automation' ? (
          <AutomationLab />
        ) : (
          <ChatBox
            messages={messages}
            loading={loading}
            speaking={speaking}
            models={models}
            model={model}
            onChooseModel={chooseModel}
            onSend={sendMessage}
            onSendVoice={sendVoice}
            onStop={stopGeneration}
          />
        )}
      </main>
    </div>
  )
}
