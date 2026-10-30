import type {
  CraCase,
  CraCaseSummary,
  KnowledgeAdminDocument,
  KnowledgeCatalog,
  RagLibrary,
  UploadPreview,
} from './types';

export const API_URL = process.env.NEXT_PUBLIC_CRA_API_URL || '/api/cra';

type ManagedFilePurpose =
  | 'case_excel'
  | 'financial_report'
  | 'knowledge_source';

type ManagedUpload = {
  fileId: string;
  status: string;
};

const managedUploadCache = new WeakMap<
  File,
  Map<ManagedFilePurpose, Promise<ManagedUpload>>
>();

export function financialReportSourceUrl(caseId: string, page?: number) {
  const base = `${API_URL}/cases/${encodeURIComponent(caseId)}/financial-report/source`;
  return page ? `${base}#page=${page}` : base;
}

export function knowledgeSourceUrl(documentId: string) {
  return `${API_URL}/knowledge/documents/${encodeURIComponent(documentId)}/source`;
}

export function knowledgePreviewUrl(documentId: string, page = 1) {
  return `${API_URL}/knowledge/documents/${encodeURIComponent(documentId)}/preview?page=${page}`;
}

async function responseError(response: Response): Promise<Error> {
  const body = await response.json().catch(() => null);
  const detail = body?.detail;
  const message =
    typeof detail === 'string'
      ? detail
      : detail?.message ||
        (Array.isArray(detail) ? detail.join('；') : '') ||
        `请求失败（${response.status}）`;
  return new Error(message);
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = 20_000
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_URL}${path}`, {
      ...init,
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!response.ok) {
      throw await responseError(response);
    }
    return response.json();
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') {
      throw new Error('读取超时，请检查 Neon 数据库连接后重试');
    }
    throw reason;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function download(path: string, fallbackFilename: string) {
  if (path.endsWith('/source')) {
    const link = document.createElement('a');
    link.href = `${API_URL}${path}`;
    link.download = fallbackFilename;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
    return fallbackFilename;
  }
  const response = await fetch(`${API_URL}${path}`, { cache: 'no-store' });
  if (!response.ok) throw await responseError(response);

  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = fallbackFilename;
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  return fallbackFilename;
}

async function fileSha256(file: File) {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    await file.arrayBuffer()
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0')
  ).join('');
}

async function uploadManagedFile(
  file: File,
  purpose: ManagedFilePurpose
): Promise<ManagedUpload> {
  let purposeCache = managedUploadCache.get(file);
  if (!purposeCache) {
    purposeCache = new Map();
    managedUploadCache.set(file, purposeCache);
  }
  const cached = purposeCache.get(purpose);
  if (cached) return cached;

  const operation = (async () => {
    const idempotencyKey = crypto.randomUUID();
    const initResponse = await fetch('/api/files/init', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({
        purpose,
        filename: file.name,
        contentType: file.type || 'application/octet-stream',
        sizeBytes: file.size,
        sha256: await fileSha256(file),
        idempotencyKey,
      }),
    });
    if (!initResponse.ok) throw await responseError(initResponse);
    const initialized = (await initResponse.json()) as {
      fileId: string;
      uploadUrl: string;
      method: 'PUT';
      headers?: Record<string, string>;
    };
    const uploadResponse = await fetch(initialized.uploadUrl, {
      method: initialized.method,
      headers: initialized.headers,
      body: file,
    });
    if (!uploadResponse.ok) {
      throw new Error(`文件上传失败（${uploadResponse.status}）`);
    }
    const completeResponse = await fetch(
      `/api/files/${encodeURIComponent(initialized.fileId)}/complete`,
      { method: 'POST' }
    );
    if (!completeResponse.ok) throw await responseError(completeResponse);
    return (await completeResponse.json()) as ManagedUpload;
  })();
  purposeCache.set(purpose, operation);
  try {
    return await operation;
  } catch (error) {
    purposeCache.delete(purpose);
    throw error;
  }
}

export const craApi = {
  cases: () => request<CraCaseSummary[]>('/cases'),
  case: (caseId: string) => request<CraCase>(`/cases/${caseId}`),
  updateCaseTitle: (caseId: string, title: string) =>
    request<CraCase>(`/cases/${caseId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  knowledge: () => request<KnowledgeCatalog>('/knowledge'),
  knowledgeDocument: (documentId: string) =>
    request<KnowledgeAdminDocument>(`/knowledge/documents/${documentId}`),
  uploadKnowledgeDocument: (options: {
    libraryId: RagLibrary['id'];
    title: string;
    file: File;
  }) =>
    uploadManagedFile(options.file, 'knowledge_source').then(({ fileId }) =>
      request<KnowledgeAdminDocument>(
        '/knowledge/documents',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            file_id: fileId,
            library_id: options.libraryId,
            title: options.title,
          }),
        },
        120_000
      )
    ),
  updateKnowledgeDocument: (
    documentId: string,
    options: {
      libraryId: RagLibrary['id'];
      title: string;
      content: string;
    }
  ) =>
    request<KnowledgeAdminDocument>(`/knowledge/documents/${documentId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        library_id: options.libraryId,
        title: options.title,
        content: options.content,
      }),
    }),
  deleteKnowledgeDocument: (documentId: string) =>
    request<{
      deleted: boolean;
      document_id: string;
      audit_recorded: boolean;
    }>(`/knowledge/documents/${documentId}`, { method: 'DELETE' }),
  preview: (file: File) =>
    uploadManagedFile(file, 'case_excel').then(({ fileId }) =>
      request<UploadPreview>(
        '/cases/preview',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_id: fileId }),
        },
        120_000
      )
    ),
  create: (file: File, options: { duplicateMode: string }) =>
    uploadManagedFile(file, 'case_excel').then(({ fileId }) =>
      request<CraCase>(
        '/cases',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            file_id: fileId,
            duplicate_mode: options.duplicateMode,
          }),
        },
        120_000
      )
    ),
  run: (caseId: string) =>
    request<CraCase>(`/cases/${caseId}/agent/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'continue' }),
    }),
  useDemoFinancials: (caseId: string) =>
    request<CraCase>(`/cases/${caseId}/financial-report/demo`, {
      method: 'POST',
    }),
  uploadFinancialReport: (caseId: string, file: File) =>
    uploadManagedFile(file, 'financial_report').then(({ fileId }) =>
      request<CraCase>(
        `/cases/${caseId}/financial-report`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_id: fileId }),
        },
        120_000
      )
    ),
  regenerateRiskOpinion: (caseId: string) =>
    request<CraCase>(`/cases/${caseId}/risk-opinion/regenerate`, {
      method: 'POST',
    }),
  saveRiskOpinion: (
    caseId: string,
    opinion: {
      company_description: string;
      risk_comments: string;
      stance?: string;
      qualitative?: Record<string, { choice: string; reason: string }>;
    }
  ) =>
    request<CraCase>(`/cases/${caseId}/risk-opinion`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(opinion),
    }),
  resolve: (caseId: string, taskId: number, resolution = {}) =>
    request<CraCase>(`/cases/${caseId}/tasks/${taskId}/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resolution }),
    }),
  chat: (
    caseId: string,
    question: string,
    history: Array<{ role: 'user' | 'assistant'; content: string }> = []
  ) =>
    request<{
      found: boolean;
      answer: string;
      sources:
        | Record<string, string>
        | Array<{ id?: string; citation?: string; text?: string }>;
      citation?: Record<string, unknown>;
      validation_attempts?: number;
      requires_human_review?: boolean;
      review_task_id?: number | null;
    }>(`/cases/${caseId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history }),
    }),
  download,
};
