/**
 * 声纹管理 API 调用封装
 */

import { apiClient } from './client'

export interface SpeakerProfile {
  speaker_id: string
  name: string | null
  role: string | null
  department: string | null
  sample_count: number
  quality: number
  registered_at: string | null
  updated_at: string | null
  is_active: boolean
  total_identifications: number
  avg_confidence: number | null
  last_recognized_at: string | null
  last_confidence: number | null
  embedding_std_mean: number | null
  embedding_std_max: number | null
}

export interface SpeakerStats {
  total: number
  active: number
  inactive: number
  db_path: string
  total_identifications: number
  today_identifications: number
  unique_speakers_identified: number
  quality_avg: number
  sample_avg: number
  department_distribution: Record<string, number>
  role_distribution: Record<string, number>
}

export interface SpeakerSimilarityPair {
  speaker_a_id: string
  speaker_a_name: string
  speaker_b_id: string
  speaker_b_name: string
  similarity: number
  distance: number
  embedding_std_a: number | null
  embedding_std_b: number | null
}

export interface SpeakerListResponse {
  speakers: SpeakerProfile[]
  stats: SpeakerStats
  total: number
}

export interface SpeakerStatsDetailResponse extends SpeakerStats {
  similarity_distribution: SpeakerSimilarityPair[]
}

export interface RegisterResponse {
  success: boolean
  speaker_id: string
  name: string
  message: string
  quality: number
  quality_level?: string
  quality_description?: string
  sample_count: number
  total_duration?: number
  quality_issues?: string[]
}

export interface DeleteResponse {
  success: boolean
  speaker_id: string
  message: string
}

export interface SupplementResponse {
  success: boolean
  speaker_id: string
  name: string
  message: string
  quality: number
  total_samples: number
  total_duration: number
}

export interface SearchResponse {
  speakers: SpeakerProfile[]
  total: number
}

// 获取说话人列表
export async function listSpeakers(): Promise<SpeakerListResponse> {
  const { data } = await apiClient.get<SpeakerListResponse>('/speakers')
  return data
}

// 获取详细统计
export async function getSpeakerStats(): Promise<SpeakerStatsDetailResponse> {
  const { data } = await apiClient.get<SpeakerStatsDetailResponse>('/speakers/stats')
  return data
}

// 搜索说话人
export async function searchSpeakers(name?: string): Promise<SearchResponse> {
  const { data } = await apiClient.get<SearchResponse>('/speakers/search', {
    params: { name },
  })
  return data
}

// 注册声纹
export async function registerSpeaker(
  name: string,
  audioFile: File,
  speakerId?: string,
  role?: string,
  department?: string,
  audioCount: number = 1
): Promise<RegisterResponse> {
  const formData = new FormData()
  formData.append('name', name)
  formData.append('audio', audioFile)
  formData.append('audio_count', String(audioCount))
  if (speakerId) formData.append('speaker_id', speakerId)
  if (role) formData.append('role', role)
  if (department) formData.append('department', department)

  try {
    const { data } = await apiClient.post<RegisterResponse>('/speakers/register', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  } catch (error: any) {
    // 解析质量错误详情
    if (error.response?.data?.detail?.error_type === 'quality_check_failed') {
      const detail = error.response.data.detail
      const errorMsg = buildQualityErrorMessage(detail)
      throw new Error(errorMsg)
    }
    throw error
  }
}

// 构建质量错误消息
function buildQualityErrorMessage(detail: any): string {
  const lines: string[] = []

  lines.push(`❌ 声纹注册失败：音频质量不满足要求`)
  lines.push(``)
  lines.push(`📊 质量评估结果：`)
  lines.push(`   质量分数：${Math.round(detail.quality_score * 100)}%`)
  lines.push(`   质量等级：${detail.quality_level}`)
  lines.push(`   评估说明：${detail.quality_description}`)
  lines.push(``)

  if (detail.errors && detail.errors.length > 0) {
    lines.push(`⚠️ 不合格项：`)
    detail.errors.forEach((err: string) => {
      lines.push(`   • ${err}`)
    })
    lines.push(``)
  }

  if (detail.quality_issues && detail.quality_issues.length > 0) {
    lines.push(`🔍 检测到的问题：`)
    detail.quality_issues.forEach((issue: string) => {
      lines.push(`   • ${issue}`)
    })
    lines.push(``)
  }

  if (detail.fix_guide) {
    lines.push(`💡 修复建议：`)
    detail.fix_guide.split('\n').forEach((line: string) => {
      lines.push(`   ${line}`)
    })
  }

  return lines.join('\n')
}

// 删除说话人
export async function deleteSpeaker(speakerId: string): Promise<DeleteResponse> {
  const { data } = await apiClient.post<DeleteResponse>('/speakers/delete', {
    speaker_id: speakerId,
  })
  return data
}

// 补充声纹音频（为已注册说话人追加新样本）
export async function supplementSpeakerAudio(
  speakerId: string,
  audioFile: File
): Promise<SupplementResponse> {
  const formData = new FormData()
  formData.append('speaker_id', speakerId)
  formData.append('audio', audioFile)

  try {
    const { data } = await apiClient.post<SupplementResponse>('/speakers/supplement', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  } catch (error: any) {
    if (error.response?.status === 404) {
      throw new Error(`未找到声纹ID「${speakerId}」，请先注册`)
    }
    throw error
  }
}
