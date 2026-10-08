import { useEffect, useRef, useState } from 'react'
import MessageBubble from './MessageBubble.jsx'
import { HandsFreeListener } from '../lib/handsfree.js'

const HF_LABELS = {
  listening: '🎙️ Hands-free: listening — just start talking',
  capturing: '🔴 Hearing you… pause when you finish the sentence',
  paused: '⏸ Hands-free paused while P2BL is busy…',
}

const SUGGESTIONS = [
  { icon: '💡', text: 'How does smart lighting work in this home?' },
  { icon: '⚡', text: 'How does the system save energy?' },
  { icon: '🛡️', text: 'How does the home handle unusual entry?' },
  { icon: '🏠', text: 'What home applications does this prototype support?' },
]

export default function ChatBox({
  messages,
  loading,
  speaking,
  models = [],
  model,
  onChooseModel,
  onSend,
  onSendVoice,
  onStop,
}) {
  const [input, setInput] = useState('')
  const [recording, setRecording] = useState(false)
  const [micError, setMicError] = useState('')
  const [handsFree, setHandsFree] = useState(false)
  const [hfState, setHfState] = useState('off')
  const [palIndex, setPalIndex] = useState(0)

  // ---- prompt history (terminal-style ↑/↓ recall), persisted locally ----
  const [promptHistory, setPromptHistory] = useState(() => {
    try {
      return JSON.parse(
        localStorage.getItem('p2bl-prompt-history') ||
          localStorage.getItem('jarvis-prompt-history') || // pre-rename key
          '[]'
      )
    } catch {
      return []
    }
  })
  const [histIndex, setHistIndex] = useState(null) // null = not browsing
  const draftRef = useRef('') // what was typed before browsing began

  function rememberPrompt(text) {
    setPromptHistory((prev) => {
      if (prev[prev.length - 1] === text) return prev // skip consecutive repeats
      const next = [...prev, text].slice(-50)
      localStorage.setItem('p2bl-prompt-history', JSON.stringify(next))
      return next
    })
  }

  // ---- slash-command palette (Claude-CLI style): type "/" to see commands,
  // "/model" to pick the AI model right from the input ----
  const slashMode = input.startsWith('/')
  const modelMode = /^\/model(\s|$)/i.test(input)
  const modelFilter = modelMode ? input.replace(/^\/model\s*/i, '').trim().toLowerCase() : ''
  const paletteItems = !slashMode
    ? []
    : modelMode
      ? models
          .filter((m) => m.name.toLowerCase().includes(modelFilter))
          .map((m) => ({ type: 'model', ...m }))
      : [{ type: 'cmd', name: '/model', desc: 'Switch the AI model' }].filter((c) =>
          c.name.startsWith(input.trim().toLowerCase())
        )

  useEffect(() => {
    setPalIndex(0)
  }, [input])

  function selectPaletteItem(item) {
    if (!item) return
    if (item.type === 'cmd') {
      setInput('/model ')
    } else {
      onChooseModel(item.name)
      setInput('')
    }
  }

  function onInputKeyDown(e) {
    // Palette open → arrows drive the palette
    if (slashMode && paletteItems.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setPalIndex((i) => (i + 1) % paletteItems.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setPalIndex((i) => (i - 1 + paletteItems.length) % paletteItems.length)
      } else if (e.key === 'Enter') {
        e.preventDefault()
        selectPaletteItem(paletteItems[palIndex])
      } else if (e.key === 'Escape') {
        setInput('')
      }
      return
    }

    // Otherwise → arrows recall previous prompts, like a terminal
    if (e.key === 'ArrowUp') {
      if (promptHistory.length === 0) return
      e.preventDefault()
      if (histIndex === null) {
        draftRef.current = input // stash whatever was being typed
        const idx = promptHistory.length - 1
        setHistIndex(idx)
        setInput(promptHistory[idx])
      } else if (histIndex > 0) {
        const idx = histIndex - 1
        setHistIndex(idx)
        setInput(promptHistory[idx])
      }
    } else if (e.key === 'ArrowDown' && histIndex !== null) {
      e.preventDefault()
      if (histIndex >= promptHistory.length - 1) {
        setHistIndex(null)
        setInput(draftRef.current) // walked past the newest → restore the draft
      } else {
        const idx = histIndex + 1
        setHistIndex(idx)
        setInput(promptHistory[idx])
      }
    }
  }
  const bottomRef = useRef(null)
  const recorderRef = useRef(null)
  const chunksRef = useRef([])
  const listenerRef = useRef(null)

  // Keep the latest onSendVoice reachable from the long-lived listener
  const onSendVoiceRef = useRef(onSendVoice)
  onSendVoiceRef.current = onSendVoice

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Start/stop the hands-free listener with the toggle
  useEffect(() => {
    if (!handsFree) return
    setMicError('')
    const listener = new HandsFreeListener({
      onUtterance: (blob) => onSendVoiceRef.current(blob),
      onState: setHfState,
    })
    listenerRef.current = listener
    listener.start().catch(() => {
      setMicError('Microphone access denied — allow it in your browser settings.')
      setHandsFree(false)
    })
    return () => {
      listener.stop()
      listenerRef.current = null
    }
  }, [handsFree])

  // Don't listen while a request is in flight or the reply is playing —
  // otherwise P2BL hears its own voice and answers itself in a loop.
  useEffect(() => {
    listenerRef.current?.setPaused(loading || speaking)
  }, [loading, speaking])

  function submit(e) {
    e.preventDefault()
    if (slashMode) return // slash commands are handled by the palette, never sent
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    rememberPrompt(text)
    setHistIndex(null)
    onSend(text)
  }

  async function toggleRecording() {
    setMicError('')
    if (recording) {
      recorderRef.current?.stop() // onstop handler sends the audio
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      // Safari records audio/mp4, Chrome audio/webm — backend handles both
      const mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : ''
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop()) // release the mic
        setRecording(false)
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        if (blob.size > 0) onSendVoice(blob)
      }
      recorderRef.current = recorder
      recorder.start()
      setRecording(true)
    } catch {
      setMicError('Microphone access denied — allow it in your browser settings.')
    }
  }

  const placeholder = handsFree
    ? 'Hands-free is on — speak anytime, or type here'
    : recording
      ? 'Listening… tap ⏹ when you finish speaking'
      : 'Type here — "/" for commands, 🎤 to talk, 🎙️ hands-free'

  return (
    <div className="chatbox">
      <div className="messages">
        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}
        {messages.length === 1 && !loading && (
          <div className="suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s.text} className="suggestion" onClick={() => onSend(s.text)}>
                <span className="suggestion-icon">{s.icon}</span>
                {s.text}
              </button>
            ))}
          </div>
        )}
        {loading && !messages[messages.length - 1]?.streaming && (
          <div className="bubble assistant thinking">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
            <span className="thinking-label">thinking / using tools…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {micError && <div className="mic-error">{micError}</div>}
      {handsFree && hfState !== 'off' && (
        <div className={`hf-status hf-${hfState}`}>{HF_LABELS[hfState]}</div>
      )}

      <form className="input-row" onSubmit={submit}>
        {slashMode && paletteItems.length > 0 && (
          <div className="palette">
            {modelMode && <div className="palette-title">Select a model</div>}
            {paletteItems.map((item, i) => (
              <button
                type="button"
                key={item.name}
                className={`palette-item ${i === palIndex ? 'active' : ''}`}
                onMouseEnter={() => setPalIndex(i)}
                onClick={() => selectPaletteItem(item)}
              >
                <span className="palette-name">{item.name}</span>
                <span className="palette-desc">
                  {item.type === 'cmd'
                    ? item.desc
                    : [
                        item.name === model ? '✓ current' : null,
                        item.supports_tools ? null : 'no tools',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                </span>
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          className={`mic-btn ${handsFree ? 'handsfree-on' : ''} ${hfState === 'capturing' ? 'recording' : ''}`}
          onClick={() => setHandsFree((v) => !v)}
          title={handsFree ? 'Turn off hands-free listening' : 'Hands-free: auto-detect your voice'}
        >
          🎙️
        </button>
        <button
          type="button"
          className={`mic-btn ${recording ? 'recording' : ''}`}
          onClick={toggleRecording}
          disabled={loading || handsFree}
          title={
            handsFree
              ? 'Tap-to-talk is off while hands-free is on'
              : recording
                ? 'Tap to stop and send'
                : 'Tap to talk once'
          }
        >
          {recording ? '⏹' : '🎤'}
        </button>
        <input
          value={input}
          onChange={(e) => {
            setInput(e.target.value)
            setHistIndex(null) // typing means you're no longer browsing history
          }}
          onKeyDown={onInputKeyDown}
          placeholder={placeholder}
          autoFocus
        />
        {loading ? (
          <button type="button" className="stop-btn" onClick={onStop} title="Stop generating">
            ⏹ Stop
          </button>
        ) : (
          <button type="submit" disabled={!input.trim()}>
            Send
          </button>
        )}
      </form>
    </div>
  )
}
