/**
 * 音频文件上传组件
 * 用于离线模式的音频文件上传
 */

import { useState, useRef, useCallback } from 'react'
import { uploadAudioFile, getMeetingStatus } from '../../api/meetings'

interface Props {
  meetingId: string
  onUploadStart?: () => void
  /** 后端处理完成时触发（所有阶段轮询结束后） */
  onProcessingComplete?: (result: { status: string; message: string }) => void
  onError?: (error: string) => void
  disabled?: boolean
}

export default function AudioUploader({
  meetingId,
  onUploadStart,
  onProcessingComplete,
  onError,
  disabled = false,
}: Props) {
  const [isUploading, setIsUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [isProcessing, setIsProcessing] = useState(false)
  const [processingStatus, setProcessingStatus] = useState<string>('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const isBusyRef = useRef(false)

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      const allowedTypes = ['audio/wav', 'audio/mpeg', 'audio/mp3', 'audio/m4a', 'audio/ogg', 'audio/webm', 'audio/flac']
      const allowedExtensions = ['.wav', '.mp3', '.m4a', '.ogg', '.webm', '.flac']
      const fileExt = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()

      if (!allowedTypes.includes(file.type) && !allowedExtensions.includes(fileExt)) {
        onError?.('不支持的文件格式。请上传 wav, mp3, m4a, ogg, webm 或 flac 格式的音频文件。')
        return
      }

      const maxSize = 500 * 1024 * 1024
      if (file.size > maxSize) {
        onError?.('文件过大。最大支持 500MB 的音频文件。')
        return
      }

      setSelectedFile(file)
      setUploadProgress(0)
    }
  }, [onError])

  const handleUpload = useCallback(async () => {
    if (!selectedFile || !meetingId) return

    if (isBusyRef.current) return
    isBusyRef.current = true

    setIsUploading(true)
    setUploadProgress(0)
    onUploadStart?.()

    try {
      setUploadProgress(10)

      await uploadAudioFile(meetingId, selectedFile, (progress) => {
        setUploadProgress(Math.min(90, 10 + progress * 0.8))
      })

      setUploadProgress(100)
      setIsUploading(false)
      setIsProcessing(true)
      setProcessingStatus('正在处理...')

      pollProcessingStatus(meetingId)
    } catch (error) {
      isBusyRef.current = false
      setIsUploading(false)
      const message = error instanceof Error ? error.message : '上传失败'
      onError?.(message)
    }
  }, [selectedFile, meetingId, onUploadStart, onProcessingComplete, onError])

  const pollProcessingStatus = async (meetingId: string) => {
    const maxAttempts = 60
    let attempts = 0

    const poll = async () => {
      if (attempts >= maxAttempts) {
        isBusyRef.current = false
        setProcessingStatus('处理超时')
        setIsProcessing(false)
        return
      }

      try {
        const status = await getMeetingStatus(meetingId)

        if (status.status === 'completed' || status.status === 'ended') {
          isBusyRef.current = false
          setProcessingStatus('处理完成')
          setIsProcessing(false)
          onProcessingComplete?.({ status: 'completed', message: '处理完成' })
        } else if (status.status === 'failed') {
          isBusyRef.current = false
          setProcessingStatus('处理失败')
          setIsProcessing(false)
          onError?.('音频处理失败')
        } else {
          setProcessingStatus(`处理中: ${status.status}`)
          attempts++
          setTimeout(poll, 5000)
        }
      } catch {
        attempts++
        setTimeout(poll, 5000)
      }
    }

    poll()
  }

  const handleCancel = useCallback(() => {
    setSelectedFile(null)
    setUploadProgress(0)
    setIsUploading(false)
    setIsProcessing(false)
    setProcessingStatus('')
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }, [])

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  return (
    <div className="flex flex-col gap-3">
      {/* 文件选择 */}
      <div className="flex items-center gap-3">
        <input
          ref={fileInputRef}
          type="file"
          accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm,.flac"
          onChange={handleFileSelect}
          disabled={disabled || isUploading || isProcessing}
          className="hidden"
        />

        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || isUploading || isProcessing}
          className={`px-4 py-2 rounded-lg border border-dashed transition-colors ${
            disabled || isUploading || isProcessing
              ? 'border-gray-600 text-gray-600 cursor-not-allowed'
              : 'border-primary-500 text-primary-400 hover:bg-primary-500/10'
          }`}
        >
          📁 选择音频文件
        </button>

        {selectedFile && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-gray-400">{selectedFile.name}</span>
            <span className="text-gray-500">({formatFileSize(selectedFile.size)})</span>
            {!isUploading && !isProcessing && (
              <button
                onClick={handleCancel}
                className="text-gray-400 hover:text-red-400 ml-2"
                title="取消选择"
              >
                ×
              </button>
            )}
          </div>
        )}
      </div>

      {/* 上传进度 */}
      {isUploading && (
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-xs text-gray-400">
            <span>上传中...</span>
            <span>{Math.round(uploadProgress)}%</span>
          </div>
          {selectedFile && (
            <div className="text-xs text-gray-500">
              文件较大时，上传速度主要取决于你的上行带宽和服务器链路。
            </div>
          )}
          <div className="h-1.5 bg-dark-300 rounded-full overflow-hidden">
            <div
              className="h-full bg-primary-500 transition-all duration-300"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* 处理状态 */}
      {isProcessing && (
        <div className="flex items-center gap-2 text-sm">
          <div className="w-4 h-4 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-gray-400">{processingStatus || '正在处理...'}</span>
        </div>
      )}

      {/* 上传按钮 */}
      {selectedFile && !isUploading && !isProcessing && (
        <button
          onClick={handleUpload}
          disabled={disabled}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            disabled
              ? 'bg-gray-600 text-gray-400 cursor-not-allowed'
              : 'bg-primary-600 hover:bg-primary-700 text-white'
          }`}
        >
          开始处理（VibeVoice）
        </button>
      )}

    </div>
  )
}
