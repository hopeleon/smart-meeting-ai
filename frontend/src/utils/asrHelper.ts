type SpeechRecognitionConstructor = new () => SpeechRecognitionLike

interface SpeechRecognitionLike {
  continuous: boolean
  interimResults: boolean
  lang: string
  maxAlternatives: number
  addEventListener: (type: string, listener: (event?: unknown) => void) => void
  start: () => void
  stop: () => void
  abort: () => void
}

interface SpeechRecognitionResultEventLike {
  resultIndex: number
  results: ArrayLike<{
    isFinal: boolean
    0: { transcript: string }
  }>
}

type SpeechWindow = Window &
  typeof globalThis & {
    SpeechRecognition?: SpeechRecognitionConstructor
    webkitSpeechRecognition?: SpeechRecognitionConstructor
  }

export interface ASRResult {
  finalText: string
  interimText: string
  transcript: string
  isFinal: boolean
}

export interface ASRHelperOptions {
  lang?: string
  continuous?: boolean
  interimResults?: boolean
  maxAlternatives?: number
  onStart?: () => void
  onResult?: (data: ASRResult) => void
  onEnd?: () => void
  onError?: (error: string) => void
}

function getSpeechRecognitionCtor(): SpeechRecognitionConstructor | undefined {
  const speechWindow = window as SpeechWindow
  return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition
}

export class ASRHelper {
  private recognition: SpeechRecognitionLike | null
  private finalText = ''
  private isListening = false
  private readonly options: Required<ASRHelperOptions>

  constructor(options: ASRHelperOptions = {}) {
    this.options = {
      lang: options.lang || 'zh-CN',
      continuous: options.continuous ?? false,
      interimResults: options.interimResults ?? true,
      maxAlternatives: options.maxAlternatives || 1,
      onStart: options.onStart || (() => {}),
      onResult: options.onResult || (() => {}),
      onEnd: options.onEnd || (() => {}),
      onError: options.onError || (() => {}),
    }
    this.recognition = this.setupRecognition()
  }

  static isSupported(): boolean {
    return Boolean(getSpeechRecognitionCtor())
  }

  start(): void {
    if (!this.recognition) {
      this.options.onError('not-supported')
      return
    }
    if (this.isListening) return

    this.finalText = ''
    try {
      this.recognition.start()
    } catch (err) {
      this.options.onError(err instanceof Error ? err.message : String(err))
    }
  }

  stop(): void {
    if (this.recognition && this.isListening) {
      this.recognition.stop()
    }
  }

  abort(): void {
    if (this.recognition && this.isListening) {
      this.recognition.abort()
    }
  }

  private setupRecognition(): SpeechRecognitionLike | null {
    const Recognition = getSpeechRecognitionCtor()
    if (!Recognition) return null

    const recognizer = new Recognition()
    recognizer.lang = this.options.lang
    recognizer.continuous = this.options.continuous
    recognizer.interimResults = this.options.interimResults
    recognizer.maxAlternatives = this.options.maxAlternatives

    recognizer.addEventListener('start', () => {
      this.isListening = true
      this.options.onStart()
    })

    recognizer.addEventListener('result', (event) => {
      const resultEvent = event as SpeechRecognitionResultEventLike
      let interimText = ''
      let finalChunk = ''

      for (let i = resultEvent.resultIndex; i < resultEvent.results.length; i += 1) {
        const transcript = resultEvent.results[i][0].transcript
        if (resultEvent.results[i].isFinal) finalChunk += transcript
        else interimText += transcript
      }

      if (finalChunk) this.finalText += finalChunk
      const transcript = `${this.finalText}${interimText}`.trim()
      this.options.onResult({
        finalText: this.finalText.trim(),
        interimText: interimText.trim(),
        transcript,
        isFinal: Boolean(finalChunk),
      })
    })

    recognizer.addEventListener('error', (event) => {
      const error = event as { error?: string; message?: string }
      this.options.onError(error.error || error.message || 'speech-recognition-error')
    })

    recognizer.addEventListener('end', () => {
      this.isListening = false
      this.options.onEnd()
    })

    return recognizer
  }
}
