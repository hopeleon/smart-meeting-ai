import { useAudioStore } from '../../stores/audioStore'

interface Props {
  meetingId: string
}

export default function RecordingControls({ meetingId }: Props) {
  const { isRecording, isPaused, setRecording, setPaused } = useAudioStore()

  const handleStart = () => {
    setRecording(true)
    setPaused(false)
    // TODO: 接入 MediaRecorder API 实现真实录音
  }

  const handlePause = () => {
    setPaused(!isPaused)
  }

  const handleStop = () => {
    setRecording(false)
    setPaused(false)
  }

  return (
    <div className="flex items-center gap-3">
      {!isRecording ? (
        <button
          onClick={handleStart}
          className="flex items-center gap-2 px-5 py-2.5 bg-red-600 rounded-full hover:bg-red-700 transition-colors"
        >
          <span className="w-3 h-3 bg-white rounded-full" />
          <span className="text-sm font-medium">开始录制</span>
        </button>
      ) : (
        <>
          <button
            onClick={handlePause}
            className="flex items-center gap-2 px-4 py-2.5 bg-dark-200 rounded-full hover:bg-dark-300 transition-colors"
          >
            {isPaused ? (
              <>
                <span className="w-3 h-3 bg-white rounded-sm" />
                <span className="text-sm">继续</span>
              </>
            ) : (
              <>
                <span className="flex gap-0.5">
                  <span className="w-1 h-3 bg-white rounded-sm" />
                  <span className="w-1 h-3 bg-white rounded-sm" />
                </span>
                <span className="text-sm">暂停</span>
              </>
            )}
          </button>
          <button
            onClick={handleStop}
            className="flex items-center gap-2 px-4 py-2.5 bg-red-600 rounded-full hover:bg-red-700 transition-colors"
          >
            <span className="w-3 h-3 bg-white rounded-sm" />
            <span className="text-sm">停止</span>
          </button>
        </>
      )}
    </div>
  )
}
