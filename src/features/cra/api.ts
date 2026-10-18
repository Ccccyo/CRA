import type {
  CraCase,
  CraCaseSummary,
  KnowledgeAdminDocument,
  KnowledgeCatalog,
  RagLibrary,
  UploadPreview,
} from './types';

export const API_URL = process.env.NEXT_PUBLIC_CRA_API_URL || '/api/cra';

export function financialReportSourceUrl(caseId: string, page?: number) {
  const base = `${API_URL}/cases/${encodeURIComponent(caseId)}/financial-report/source`;
  return page ? `${base}#page=${page}` : base;
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

export const craApi = {
  cases: () => request<CraCaseSummary[]>('/cases'),
  case: (caseId: string) => request<CraCase>(`/cases/${caseId}`),
  knowledge: () => request<KnowledgeCatalog>('/knowledge'),
  knowledgeDocument: (documentId: string) =>
    request<KnowledgeAdminDocument>(`/knowledge/documents/${documentId}`),
  uploadKnowledgeDocument: (options: {
    libraryId: RagLibrary['id'];
    title: string;
    file: File;
  }) => {
    const form = new FormData();
    form.append('library_id', options.libraryId);
    form.append('title', options.title);
    form.append('file', options.file);
    return request<KnowledgeAdminDocument>(
      '/knowledge/documents',
      { method: 'POST', body: form },
      120_000
    );
  },
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
    request<{ deleted: boolean; document_id: string }>(
      `/knowledge/documents/${documentId}`,
      { method: 'DELETE' }
    ),
  preview: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<UploadPreview>(
      '/cases/preview',
      { method: 'POST', body: form },
      120_000
    );
  },
  create: (
    file: File,
    options: { owner: string; dueDate: string; duplicateMode: string }
  ) => {
    const form = new FormData();
    form.append('file', file);
    form.append('owner', options.owner);
    form.append('due_date', options.dueDate);
    form.append('duplicate_mode', options.duplicateMode);
    return request<CraCase>('/cases', { method: 'POST', body: form }, 120_000);
  },
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
  uploadFinancialReport: (caseId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<CraCase>(
      `/cases/${caseId}/financial-report`,
      { method: 'POST', body: form },
      120_000
    );
  },
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
      sources: Record<string, string> | Array<Record<string, string>>;
      citation?: Record<string, unknown>;
    }>(`/cases/${caseId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history }),
    }),
  download,
};
