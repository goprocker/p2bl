// Hands-free voice capture.
//
// Watches the microphone continuously, detects when you start speaking
// (voice activity detection on RMS energy with an adaptive noise floor),
// captures the sentence with a pre-roll buffer so the first word isn't
// clipped, and emits a 16 kHz WAV blob when you stop talking.
//
// VadMachine is pure JS (no browser APIs) so it can be unit-tested;
// HandsFreeListener wires it to getUserMedia / Web Audio.

// --- tunables (chunk = 4096 samples ≈ 85 ms at 48 kHz) ---
const PREROLL_CHUNKS = 8 // ~0.7 s kept from before speech onset
const ONSET_CHUNKS = 2 // consecutive loud chunks that count as "speech started"
const SILENCE_CHUNKS = 16 // ~1.4 s of quiet ends the utterance
const MIN_SPEECH_CHUNKS = 4 // discard blips shorter than ~0.35 s (coughs, clicks)
const ABS_THRESHOLD = 0.012 // minimum RMS that ever counts as speech
const NOISE_FACTOR = 3.5 // speech threshold = ambient noise floor × this
const NOISE_EMA = 0.05 // smoothing for the ambient noise tracker
const CHUNK_SIZE = 4096
const TARGET_RATE = 16000 // Whisper's native rate; keeps uploads small

function rmsOf(chunk) {
  let sum = 0
  for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i]
  return Math.sqrt(sum / chunk.length)
}

function concat(chunks) {
  const total = chunks.reduce((n, c) => n + c.length, 0)
  const out = new Float32Array(total)
  let off = 0
  for (const c of chunks) {
    out.set(c, off)
    off += c.length
  }
  return out
}

export class VadMachine {
  constructor() {
    this.noiseFloor = 0.005
    this.reset()
  }

  get threshold() {
    return Math.max(ABS_THRESHOLD, this.noiseFloor * NOISE_FACTOR)
  }

  reset() {
    this.preroll = []
    this.captured = []
    this.capturing = false
    this.speechRun = 0
    this.silenceRun = 0
    this.speechChunks = 0
  }

  // Feed one chunk of mono Float32 samples.
  // Returns null, {type:'start'}, {type:'discard'}, or {type:'utterance', samples}.
  feed(chunk) {
    const rms = rmsOf(chunk)

    if (!this.capturing) {
      this.preroll.push(chunk)
      if (this.preroll.length > PREROLL_CHUNKS) this.preroll.shift()

      if (rms > this.threshold) {
        this.speechRun++
        if (this.speechRun >= ONSET_CHUNKS) {
          this.capturing = true
          this.captured = [...this.preroll] // includes the onset chunks
          this.speechChunks = this.speechRun
          this.silenceRun = 0
          return { type: 'start' }
        }
      } else {
        this.speechRun = 0
        // Learn the room's ambient level only while idle and quiet
        this.noiseFloor = this.noiseFloor * (1 - NOISE_EMA) + rms * NOISE_EMA
      }
      return null
    }

    this.captured.push(chunk)
    if (rms > this.threshold * 0.7) {
      // hysteresis: brief dips mid-sentence still count as speech
      this.silenceRun = 0
      this.speechChunks++
    } else {
      this.silenceRun++
      if (this.silenceRun >= SILENCE_CHUNKS) {
        const enough = this.speechChunks >= MIN_SPEECH_CHUNKS
        const samples = enough ? concat(this.captured) : null
        this.reset()
        return enough ? { type: 'utterance', samples } : { type: 'discard' }
      }
    }
    return null
  }
}

export function resample(samples, fromRate, toRate) {
  if (fromRate === toRate) return samples
  const outLen = Math.round((samples.length * toRate) / fromRate)
  const out = new Float32Array(outLen)
  const ratio = (samples.length - 1) / (outLen - 1)
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio
    const i0 = Math.floor(pos)
    const i1 = Math.min(i0 + 1, samples.length - 1)
    const frac = pos - i0
    out[i] = samples[i0] * (1 - frac) + samples[i1] * frac
  }
  return out
}

// Float32 [-1, 1] → 16-bit PCM mono WAV (ArrayBuffer)
export function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const writeStr = (off, s) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i))
  }
  writeStr(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeStr(8, 'WAVE')
  writeStr(12, 'fmt ')
  view.setUint32(16, 16, true) // fmt chunk size
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true) // byte rate
  view.setUint16(32, 2, true) // block align
  view.setUint16(34, 16, true) // bits per sample
  writeStr(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  let off = 44
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return buffer
}

export class HandsFreeListener {
  constructor({ onUtterance, onState }) {
    this.onUtterance = onUtterance
    this.onState = onState // 'off' | 'listening' | 'capturing' | 'paused'
    this.vad = new VadMachine()
    this.paused = false
    this.ctx = null
    this.stream = null
    this.source = null
    this.node = null
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      // echoCancellation lets the browser subtract P2BL's own reply audio;
      // we ALSO pause the VAD while the reply plays, as a second layer.
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    this.ctx = new AudioContext()
    if (this.ctx.state === 'suspended') await this.ctx.resume()
    this.source = this.ctx.createMediaStreamSource(this.stream)
    // ScriptProcessor is deprecated but universally supported and plenty
    // for VAD; we never write to the output, so nothing is played back.
    this.node = this.ctx.createScriptProcessor(CHUNK_SIZE, 1, 1)
    this.node.onaudioprocess = (e) => this._onAudio(e)
    this.source.connect(this.node)
    this.node.connect(this.ctx.destination) // required for onaudioprocess to fire
    this.onState(this.paused ? 'paused' : 'listening')
  }

  _onAudio(e) {
    if (this.paused || !this.ctx) return
    // Copy — the underlying buffer is reused between callbacks
    const chunk = new Float32Array(e.inputBuffer.getChannelData(0))
    const event = this.vad.feed(chunk)
    if (!event) return
    if (event.type === 'start') this.onState('capturing')
    if (event.type === 'discard') this.onState('listening')
    if (event.type === 'utterance') {
      this.onState('listening')
      const wav = encodeWav(resample(event.samples, this.ctx.sampleRate, TARGET_RATE), TARGET_RATE)
      this.onUtterance(new Blob([wav], { type: 'audio/wav' }))
    }
  }

  setPaused(paused) {
    this.paused = paused
    if (paused) this.vad.reset() // drop any half-captured audio
    if (this.ctx) this.onState(paused ? 'paused' : 'listening')
  }

  stop() {
    this.node?.disconnect()
    this.source?.disconnect()
    this.stream?.getTracks().forEach((t) => t.stop())
    this.ctx?.close()
    this.ctx = this.stream = this.source = this.node = null
    this.vad.reset()
    this.onState('off')
  }
}
