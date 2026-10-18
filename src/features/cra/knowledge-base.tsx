'use client';

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import {
  IconAlertTriangle,
  IconBook2,
  IconBuildingBank,
  IconCheck,
  IconDatabase,
  IconEdit,
  IconFileText,
  IconHistory,
  IconLoader2,
  IconLock,
  IconSearch,
  IconShieldCheck,
  IconTrash,
  IconUpload,
  IconWorld,
  IconX,
} from '@tabler/icons-react';
import { toast } from 'sonner';

import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { Textarea } from '@/shared/components/ui/textarea';

import { craApi } from './api';
import type {
  KnowledgeAdminDocument,
  KnowledgeCatalog,
  RagDocument,
  RagLibrary,
} from './types';

const libraryIcons = {
  policy: IconBuildingBank,
  public_reference: IconWorld,
  case_history: IconHistory,
};

type ManagerMode = 'upload' | 'edit' | null;

function LibraryNavItem({
  library,
  active,
  onSelect,
}: {
  library: RagLibrary;
  active: boolean;
  onSelect: () => void;
}) {
  const Icon = libraryIcons[library.id];
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full px-4 py-4 text-left transition-colors focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none focus-visible:ring-inset ${
        active ? 'bg-blue-50' : 'hover:bg-slate-50'
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg ${
            active
              ? 'bg-blue-700 text-white'
              : library.accessible
                ? 'bg-slate-100 text-blue-700'
                : 'bg-slate-100 text-slate-400'
          }`}
        >
          <Icon size={18} stroke={1.8} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center justify-between gap-2">
            <strong className="text-sm font-semibold text-slate-950">
              {library.label}
            </strong>
            {!library.accessible && (
              <IconLock size={14} className="shrink-0 text-slate-400" />
            )}
          </span>
          <span className="mt-1 block text-[11px] leading-5 text-slate-500">
            {library.document_count} 份文档 · {library.chunk_count} 个知识分段
          </span>
        </span>
      </div>
    </button>
  );
}

function DocumentNavItem({
  document,
  active,
  onSelect,
}: {
  document: RagDocument;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full border-b border-slate-200 px-4 py-3.5 text-left transition-colors last:border-b-0 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none focus-visible:ring-inset ${
        active ? 'bg-slate-100' : 'hover:bg-slate-50'
      }`}
    >
      <span className="flex items-start gap-2.5">
        <IconFileText
          size={16}
          className={`mt-0.5 shrink-0 ${active ? 'text-blue-700' : 'text-slate-400'}`}
        />
        <span className="min-w-0 flex-1">
          <strong className="block text-xs leading-5 font-semibold break-words text-slate-900">
            {document.title}
          </strong>
          <span className="mt-1 block text-[10px] text-slate-500 tabular-nums">
            {document.chunk_count} 个分段
          </span>
        </span>
      </span>
    </button>
  );
}

function DocumentReader({ document }: { document: RagDocument }) {
  return (
    <div className="min-w-0">
      <header className="border-b border-slate-200 px-5 py-5 md:px-7">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-blue-50 text-blue-700">
            <IconBook2 size={18} />
          </span>
          <div className="min-w-0">
            <h2 className="text-base leading-6 font-semibold break-words text-slate-950">
              {document.title}
            </h2>
            <p className="mt-1 text-[11px] leading-5 break-all text-slate-500">
              {document.id} · {document.chunk_count} 个知识分段
            </p>
          </div>
        </div>
      </header>
      <div className="divide-y divide-slate-200">
        {document.chunks.map((chunk, index) => (
          <section key={chunk.id} className="px-5 py-6 md:px-7">
            <div className="mb-3 flex flex-wrap items-center gap-2 text-[10px]">
              <span className="rounded-md bg-blue-50 px-2 py-1 font-semibold text-blue-800 tabular-nums">
                分段 {index + 1}
              </span>
              {chunk.section && (
                <span className="text-slate-500">{chunk.section}</span>
              )}
              {chunk.page && (
                <span className="text-slate-500">第 {chunk.page} 页</span>
              )}
            </div>
            <p className="max-w-[78ch] text-sm leading-7 break-words whitespace-pre-wrap text-slate-700">
              {chunk.text}
            </p>
            <p className="mt-4 text-[10px] break-all text-slate-400">
              引用：{chunk.citation}
            </p>
          </section>
        ))}
      </div>
    </div>
  );
}

function KnowledgeManager({
  mode,
  libraries,
  loading,
  saving,
  error,
  document,
  libraryId,
  title,
  content,
  file,
  confirmDelete,
  onLibraryChange,
  onTitleChange,
  onContentChange,
  onFileChange,
  onSubmit,
  onCancel,
  onRequestDelete,
  onCancelDelete,
  onDelete,
}: {
  mode: Exclude<ManagerMode, null>;
  libraries: RagLibrary[];
  loading: boolean;
  saving: boolean;
  error: string;
  document: KnowledgeAdminDocument | null;
  libraryId: RagLibrary['id'];
  title: string;
  content: string;
  file: File | null;
  confirmDelete: boolean;
  onLibraryChange: (value: RagLibrary['id']) => void;
  onTitleChange: (value: string) => void;
  onContentChange: (value: string) => void;
  onFileChange: (file: File | null) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
}) {
  if (loading) {
    return (
      <div className="space-y-4 p-5 md:p-7">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="p-5 md:p-7">
      <div className="flex flex-col gap-4 border-b border-slate-200 pb-5 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-[-0.02em] text-slate-950">
            {mode === 'upload' ? '上传知识文档' : '编辑知识文档'}
          </h2>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">
            {mode === 'upload'
              ? '支持 Markdown、TXT 和带文字层的 PDF。保存后，后台会按标题与段落自动重建检索分段。'
              : '保存会替换该文档的原文，并在同一事务中删除旧分段、生成新分段。'}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          <IconX /> 关闭维护
        </Button>
      </div>

      {document?.source_reconstructed && (
        <div className="mt-5 flex items-start gap-3 rounded-lg bg-amber-50 p-4 text-xs leading-5 text-amber-950 ring-1 ring-amber-200">
          <IconAlertTriangle size={17} className="mt-0.5 shrink-0" />
          <p>
            这是系统内置的旧文档，数据库未保存原始源文件；编辑器显示的是已有分段合并文本。首次保存后将成为可持续维护的数据库原文。
          </p>
        </div>
      )}

      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <label className="block">
          <span className="text-xs font-semibold text-slate-800">知识空间</span>
          <select
            value={libraryId}
            onChange={(event) =>
              onLibraryChange(event.target.value as RagLibrary['id'])
            }
            disabled={saving}
            className="mt-2 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 outline-none focus-visible:border-blue-600 focus-visible:ring-2 focus-visible:ring-blue-600/20 disabled:opacity-60"
          >
            {libraries.map((library) => (
              <option key={library.id} value={library.id}>
                {library.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs font-semibold text-slate-800">文档标题</span>
          <Input
            value={title}
            onChange={(event) => onTitleChange(event.target.value)}
            maxLength={160}
            required
            disabled={saving}
            placeholder="例如：集团信用政策 2026"
            className="mt-2 h-10 bg-white"
          />
          <span className="mt-1.5 block text-[11px] text-slate-500">
            {title.length}/160
          </span>
        </label>
      </div>

      {mode === 'upload' ? (
        <label className="mt-5 block">
          <span className="text-xs font-semibold text-slate-800">源文件</span>
          <Input
            type="file"
            accept=".md,.txt,.pdf,text/markdown,text/plain,application/pdf"
            required
            disabled={saving}
            onChange={(event) => onFileChange(event.target.files?.[0] || null)}
            className="mt-2 h-auto min-h-11 cursor-pointer bg-white py-2 file:mr-3 file:cursor-pointer"
          />
          <span className="mt-1.5 block text-[11px] leading-5 text-slate-500">
            单个文件不超过 10 MB；扫描 PDF 请先完成 OCR。
            {file ? ` 已选择：${file.name}` : ''}
          </span>
        </label>
      ) : (
        <label className="mt-5 block">
          <span className="text-xs font-semibold text-slate-800">原文内容</span>
          <Textarea
            value={content}
            onChange={(event) => onContentChange(event.target.value)}
            required
            disabled={saving}
            maxLength={2_000_000}
            className="mt-2 min-h-[360px] resize-y bg-white font-sans text-base leading-7 md:text-sm"
            placeholder="使用 Markdown 标题组织章节，后台会据此保留分段层级。"
          />
          <span className="mt-1.5 block text-[11px] text-slate-500 tabular-nums">
            {content.length.toLocaleString('zh-CN')} / 2,000,000 字符
          </span>
        </label>
      )}

      {error && (
        <div
          role="alert"
          className="mt-5 flex items-start gap-3 rounded-lg bg-red-50 p-4 text-xs leading-5 text-red-950 ring-1 ring-red-200"
        >
          <IconAlertTriangle size={17} className="mt-0.5 shrink-0" />
          <p>{error}</p>
        </div>
      )}

      {confirmDelete && (
        <div className="mt-5 flex flex-col gap-3 rounded-lg bg-red-50 p-4 ring-1 ring-red-200 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-red-950">
              删除文档及全部检索分段？
            </p>
            <p className="mt-1 text-xs leading-5 text-red-800">
              此操作无法撤销，后续 Agent 将不再检索到该文档。
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button type="button" variant="outline" onClick={onCancelDelete}>
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={saving}
              onClick={onDelete}
            >
              {saving ? (
                <IconLoader2 className="animate-spin" />
              ) : (
                <IconTrash />
              )}
              确认删除
            </Button>
          </div>
        </div>
      )}

      <div className="mt-6 flex flex-col-reverse gap-3 border-t border-slate-200 pt-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          {mode === 'edit' && !confirmDelete && (
            <Button
              type="button"
              variant="ghost"
              className="text-red-700 hover:bg-red-50 hover:text-red-800"
              disabled={saving}
              onClick={onRequestDelete}
            >
              <IconTrash /> 删除文档
            </Button>
          )}
        </div>
        <div className="flex gap-3">
          <Button
            type="button"
            variant="outline"
            disabled={saving}
            onClick={onCancel}
          >
            取消
          </Button>
          <Button type="submit" disabled={saving || confirmDelete}>
            {saving ? (
              <IconLoader2 className="animate-spin" />
            ) : mode === 'upload' ? (
              <IconUpload />
            ) : (
              <IconCheck />
            )}
            {saving
              ? '正在处理分段'
              : mode === 'upload'
                ? '上传并分段'
                : '保存并重建分段'}
          </Button>
        </div>
      </div>
    </form>
  );
}

export function KnowledgeBase() {
  const [catalog, setCatalog] = useState<KnowledgeCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [libraryId, setLibraryId] = useState<RagLibrary['id']>('policy');
  const [documentId, setDocumentId] = useState('');
  const [managerMode, setManagerMode] = useState<ManagerMode>(null);
  const [managerLoading, setManagerLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [managerError, setManagerError] = useState('');
  const [adminDocument, setAdminDocument] =
    useState<KnowledgeAdminDocument | null>(null);
  const [draftLibraryId, setDraftLibraryId] =
    useState<RagLibrary['id']>('policy');
  const [draftTitle, setDraftTitle] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [draftFile, setDraftFile] = useState<File | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const loadCatalog = useCallback(
    async (preferred?: {
      libraryId?: RagLibrary['id'];
      documentId?: string;
    }) => {
      const result = await craApi.knowledge();
      setCatalog(result);
      const firstAccessible =
        result.libraries.find(
          (item) => item.id === preferred?.libraryId && item.accessible
        ) ||
        result.libraries.find((item) => item.accessible) ||
        result.libraries[0];
      setLibraryId(firstAccessible.id);
      const preferredDocument = firstAccessible.documents.find(
        (item) => item.id === preferred?.documentId
      );
      setDocumentId(
        preferredDocument?.id || firstAccessible.documents[0]?.id || ''
      );
      return result;
    },
    []
  );

  useEffect(() => {
    loadCatalog()
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : '无法读取知识库')
      )
      .finally(() => setLoading(false));
  }, [loadCatalog]);

  const library =
    catalog?.libraries.find((item) => item.id === libraryId) || null;
  const documents = useMemo(() => {
    if (!library?.accessible) return [];
    const keyword = query.trim().toLocaleLowerCase();
    if (!keyword) return library.documents;
    return library.documents.filter((document) =>
      [
        document.title,
        document.citation,
        document.preview,
        ...document.chunks.map((chunk) => `${chunk.section} ${chunk.text}`),
      ]
        .join(' ')
        .toLocaleLowerCase()
        .includes(keyword)
    );
  }, [library, query]);
  const document =
    documents.find((item) => item.id === documentId) || documents[0] || null;

  const closeManager = () => {
    setManagerMode(null);
    setManagerLoading(false);
    setManagerError('');
    setAdminDocument(null);
    setDraftTitle('');
    setDraftContent('');
    setDraftFile(null);
    setConfirmDelete(false);
  };

  const selectLibrary = (next: RagLibrary) => {
    setLibraryId(next.id);
    setDocumentId(next.documents[0]?.id || '');
    setQuery('');
    closeManager();
  };

  const openUpload = () => {
    setManagerMode('upload');
    setDraftLibraryId(library?.accessible ? library.id : 'policy');
    setDraftTitle('');
    setDraftContent('');
    setDraftFile(null);
    setAdminDocument(null);
    setManagerError('');
    setConfirmDelete(false);
  };

  const openEdit = async () => {
    if (!document) return;
    setManagerMode('edit');
    setManagerLoading(true);
    setManagerError('');
    setConfirmDelete(false);
    try {
      const result = await craApi.knowledgeDocument(document.id);
      setAdminDocument(result);
      setDraftLibraryId(result.library_id);
      setDraftTitle(result.title);
      setDraftContent(result.source_content);
    } catch (reason) {
      setManagerError(
        reason instanceof Error ? reason.message : '无法读取文档原文'
      );
    } finally {
      setManagerLoading(false);
    }
  };

  const submitManager = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving) return;
    const title = draftTitle.trim();
    if (!title) {
      setManagerError('请输入文档标题。');
      return;
    }
    if (managerMode === 'upload' && !draftFile) {
      setManagerError('请选择需要上传的 Markdown、TXT 或 PDF 文件。');
      return;
    }
    if (managerMode === 'edit' && !draftContent.trim()) {
      setManagerError('文档原文不能为空。');
      return;
    }

    setSaving(true);
    setManagerError('');
    try {
      const result =
        managerMode === 'upload'
          ? await craApi.uploadKnowledgeDocument({
              libraryId: draftLibraryId,
              title,
              file: draftFile!,
            })
          : await craApi.updateKnowledgeDocument(adminDocument!.id, {
              libraryId: draftLibraryId,
              title,
              content: draftContent,
            });
      await loadCatalog({
        libraryId: result.library_id,
        documentId: result.id,
      });
      closeManager();
      toast.success(
        managerMode === 'upload'
          ? `已上传并生成 ${result.chunk_count} 个知识分段`
          : `已保存并重建 ${result.chunk_count} 个知识分段`
      );
    } catch (reason) {
      setManagerError(
        reason instanceof Error ? reason.message : '知识文档保存失败'
      );
    } finally {
      setSaving(false);
    }
  };

  const deleteDocument = async () => {
    if (!adminDocument || saving) return;
    setSaving(true);
    setManagerError('');
    try {
      await craApi.deleteKnowledgeDocument(adminDocument.id);
      await loadCatalog({ libraryId: adminDocument.library_id });
      closeManager();
      toast.success('知识文档及其检索分段已删除');
    } catch (reason) {
      setManagerError(
        reason instanceof Error ? reason.message : '知识文档删除失败'
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="mx-auto max-w-[1440px] space-y-6 px-4 py-7 md:px-7 md:py-9">
        <Skeleton className="h-20 w-full rounded-xl" />
        <Skeleton className="h-[620px] w-full rounded-xl" />
      </div>
    );
  }

  if (error || !catalog) {
    return (
      <div className="mx-auto max-w-[1440px] px-4 py-9 md:px-7">
        <div className="flex min-h-64 items-center justify-center rounded-xl bg-white p-8 text-center ring-1 ring-slate-200">
          <div>
            <IconAlertTriangle size={28} className="mx-auto text-red-600" />
            <h1 className="mt-3 text-base font-semibold">
              无法读取 RAG 知识库
            </h1>
            <p className="mt-2 text-sm text-slate-600">{error}</p>
            <Button
              type="button"
              variant="outline"
              className="mt-5"
              onClick={() => window.location.reload()}
            >
              重新加载
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-7 md:px-7 md:py-9">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.03em] text-slate-950 md:text-3xl">
            RAG 知识库
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
            查看 Agent
            在案件中可检索的制度、公开资料与历史经验。页面只返回当前账号有权读取的正文。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-2 rounded-lg bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-800">
            <IconShieldCheck size={16} />
            {catalog.principal.role}
          </span>
          <span className="text-xs text-slate-500">
            可访问{' '}
            <strong className="font-semibold text-slate-800 tabular-nums">
              {catalog.granted_library_count}/{catalog.libraries.length}
            </strong>{' '}
            个库
          </span>
          {catalog.can_manage && (
            <Button type="button" onClick={openUpload}>
              <IconUpload /> 上传文档
            </Button>
          )}
        </div>
      </header>

      <section
        className="mt-7 overflow-hidden rounded-xl bg-white ring-1 ring-slate-200"
        aria-label="RAG 知识内容"
      >
        <div className="grid min-h-[660px] lg:grid-cols-[270px_minmax(0,1fr)]">
          <aside className="border-b border-slate-200 bg-slate-50/60 lg:border-r lg:border-b-0">
            <div className="border-b border-slate-200 px-4 py-4">
              <div className="flex items-center gap-2">
                <IconDatabase size={17} className="text-blue-700" />
                <h2 className="text-sm font-semibold text-slate-950">
                  知识空间
                </h2>
              </div>
              <p className="mt-1 text-[11px] leading-5 text-slate-500">
                权限在服务端过滤，锁定库不返回正文。
              </p>
            </div>
            <div className="divide-y divide-slate-200">
              {catalog.libraries.map((item) => (
                <LibraryNavItem
                  key={item.id}
                  library={item}
                  active={item.id === libraryId}
                  onSelect={() => selectLibrary(item)}
                />
              ))}
            </div>
          </aside>

          {!library?.accessible ? (
            <div className="grid min-h-[520px] place-items-center px-6 py-12 text-center">
              <div className="max-w-md">
                <span className="mx-auto grid size-12 place-items-center rounded-xl bg-slate-100 text-slate-400">
                  <IconLock size={23} />
                </span>
                <h2 className="mt-4 text-base font-semibold text-slate-950">
                  {library?.label}尚未授权
                </h2>
                <p className="mt-2 text-sm leading-6 text-slate-600">
                  该库包含 {library?.document_count} 份文档、
                  {library?.chunk_count} 个分段。需要
                  {library?.required_role}，可联系区域信用经理申请。
                </p>
                <p className="mt-3 text-xs leading-5 text-slate-500">
                  未授权正文不会发送到当前浏览器，也不会供本账号的 Agent 检索。
                </p>
              </div>
            </div>
          ) : (
            <div className="min-w-0">
              <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-4 xl:flex-row xl:items-center xl:justify-between">
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold text-slate-950">
                    {library.label}
                  </h2>
                  <p className="mt-1 text-[11px] text-slate-500">
                    {library.description}
                  </p>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <label className="flex h-9 w-full items-center gap-2 rounded-lg bg-slate-100 px-3 text-xs text-slate-600 ring-1 ring-transparent focus-within:bg-white focus-within:ring-blue-600 sm:w-72">
                    <IconSearch size={15} />
                    <span className="sr-only">搜索当前知识库</span>
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="搜索标题或正文"
                      className="min-w-0 flex-1 bg-transparent text-slate-900 outline-none placeholder:text-slate-500"
                    />
                  </label>
                  {catalog.can_manage && document && !managerMode && (
                    <Button type="button" variant="outline" onClick={openEdit}>
                      <IconEdit /> 编辑当前文档
                    </Button>
                  )}
                </div>
              </div>

              {documents.length || managerMode ? (
                <div className="grid min-h-[588px] md:grid-cols-[260px_minmax(0,1fr)]">
                  <nav
                    className="border-b border-slate-200 bg-slate-50/40 md:border-r md:border-b-0"
                    aria-label={`${library.label}文档`}
                  >
                    {documents.map((item) => (
                      <DocumentNavItem
                        key={item.id}
                        document={item}
                        active={item.id === document?.id}
                        onSelect={() => {
                          setDocumentId(item.id);
                          closeManager();
                        }}
                      />
                    ))}
                    {!documents.length && (
                      <p className="p-5 text-xs leading-5 text-slate-500">
                        当前知识空间还没有文档。管理员可以从右侧上传第一份资料。
                      </p>
                    )}
                  </nav>
                  {managerMode ? (
                    <KnowledgeManager
                      mode={managerMode}
                      libraries={catalog.libraries.filter(
                        (item) => item.accessible
                      )}
                      loading={managerLoading}
                      saving={saving}
                      error={managerError}
                      document={adminDocument}
                      libraryId={draftLibraryId}
                      title={draftTitle}
                      content={draftContent}
                      file={draftFile}
                      confirmDelete={confirmDelete}
                      onLibraryChange={setDraftLibraryId}
                      onTitleChange={setDraftTitle}
                      onContentChange={setDraftContent}
                      onFileChange={(nextFile) => {
                        setDraftFile(nextFile);
                        if (nextFile && !draftTitle) {
                          setDraftTitle(nextFile.name.replace(/\.[^.]+$/, ''));
                        }
                      }}
                      onSubmit={submitManager}
                      onCancel={closeManager}
                      onRequestDelete={() => setConfirmDelete(true)}
                      onCancelDelete={() => setConfirmDelete(false)}
                      onDelete={() => void deleteDocument()}
                    />
                  ) : (
                    document && <DocumentReader document={document} />
                  )}
                </div>
              ) : (
                <div className="grid min-h-[500px] place-items-center px-6 text-center">
                  <div>
                    <IconSearch size={24} className="mx-auto text-slate-400" />
                    <h3 className="mt-3 text-sm font-semibold text-slate-900">
                      {query ? '没有匹配内容' : '当前知识空间尚无文档'}
                    </h3>
                    <p className="mt-2 text-xs text-slate-500">
                      {query
                        ? '请尝试搜索制度名称、评分字段或风险主题。'
                        : catalog.can_manage
                          ? '上传 Markdown、TXT 或 PDF，后台会自动生成知识分段。'
                          : '请联系管理员维护该知识空间。'}
                    </p>
                    {catalog.can_manage && !query && (
                      <Button
                        type="button"
                        className="mt-5"
                        onClick={openUpload}
                      >
                        <IconUpload /> 上传第一份文档
                      </Button>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
