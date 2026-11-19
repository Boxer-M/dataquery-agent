/** Per-result, memory-only export receipt. Failed downloads never re-create an Artifact. */
export type Artifact = {
  artifact_id: string; query_id: string; filename: string; sha256: string; byte_size: number; created_at: string
}
export type ExportStage = 'idle' | 'creating' | 'downloading' | 'completed' | 'failed'
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i

export function artifactReceipt(value: unknown, queryId: string): Artifact {
  const item = value as Partial<Artifact> | null
  if (!item || typeof item.artifact_id !== 'string' || !uuid.test(item.artifact_id)
      || item.query_id !== queryId || item.filename !== `metrics-${item.artifact_id}.xlsx`
      || typeof item.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(item.sha256)
      || typeof item.byte_size !== 'number' || !Number.isSafeInteger(item.byte_size)
      || item.byte_size <= 0 || item.byte_size > 1024 * 1024 || typeof item.created_at !== 'string') {
    throw new Error('导出回执不完整或与当前查询不匹配。')
  }
  return item as Artifact
}

export function exportSession(queryId: string, tools: {
  create: (queryId: string) => Promise<Artifact>
  download: (artifact: Artifact) => Promise<Blob>
  save: (blob: Blob, filename: string) => void
}) {
  let artifact: Artifact | undefined
  let pending: Promise<void> | undefined
  return {
    hasArtifact: () => Boolean(artifact),
    run(stage: (value: ExportStage) => void) {
      if (pending) return pending
      pending = (async () => {
        try {
          if (!artifact) {
            stage('creating')
            artifact = artifactReceipt(await tools.create(queryId), queryId)
          }
          stage('downloading')
          const blob = await tools.download(artifact)
          tools.save(blob, artifact.filename)
          stage('completed')
        } catch (error) {
          stage('failed')
          throw error
        }
      })().finally(() => { pending = undefined })
      return pending
    },
  }
}
