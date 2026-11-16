import { jsonRequest, RequestError } from './api'

type PageSummary = {
  page: number
  method: string
  status: 'ok' | 'no_text' | 'partial' | 'error'
  error?: string | null
  extractor_version?: string | null
}
export type DocumentPage = PageSummary & {
  text: string
  reviewed_text?: string | null
  blocks: { text: string; confidence?: number; bounds?: number[] }[]
}
export type DocumentReceipt = {
  document_id: string
  version: string
  file_sha256: string
  state: 'unreviewed' | 'reviewed' | 'published'
  pages: PageSummary[]
  title?: string | null
  review_basis?: string | null
  review_version?: string | null
  publication?: { document_id: string; version: string; indexed: boolean } | null
  page?: DocumentPage
}

const base = '/api/documents'

async function documentRequest(suffix: string, method: 'GET' | 'POST', body?: object, id?: string) {
  const result = await jsonRequest<DocumentReceipt>(suffix, method, body)
  if (typeof result.document_id !== 'string' || (id && result.document_id !== id)
      || typeof result.version !== 'string' || !Array.isArray(result.pages)
      || !['unreviewed', 'reviewed', 'published'].includes(result.state)
      || (result.state !== 'unreviewed' && !result.review_version)
      || (result.state === 'published' && !result.publication?.version)) {
    throw new RequestError('文档 API 回执不完整，请刷新文档状态。', false)
  }
  return result
}

const documentPath = (id: string) => `${base}/${encodeURIComponent(id)}`
export const uploadPdf = (file: File, id?: string) =>
  documentRequest(id ? `${documentPath(id)}/versions` : base, 'POST', file, id)
export const readDocument = (id: string) => documentRequest(documentPath(id), 'GET', undefined, id)
export const readDocumentPage = (id: string, page: number) =>
  documentRequest(`${documentPath(id)}/pages/${page}`, 'GET', undefined, id)
export const reviewPdf = (document: DocumentReceipt, fields: {
  title: string; basis: string; corrections: Record<number, string>
}) => documentRequest(`${documentPath(document.document_id)}/review`, 'POST',
  { version: document.version, ...fields }, document.document_id)
export const publishPdf = (document: DocumentReceipt) =>
  documentRequest(`${documentPath(document.document_id)}/publish`, 'POST', {
    version: document.version, review_version: document.review_version, external_embedding_approved: true,
  }, document.document_id)
