'use client';

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconBooks,
  IconBuildingBank,
  IconCheck,
  IconChevronLeft,
  IconChevronRight,
  IconEdit,
  IconExternalLink,
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
import MarkdownIt from 'markdown-it';
import { toast } from 'sonner';

import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { Textarea } from '@/shared/components/ui/textarea';

import { craApi, knowledgePreviewUrl, knowledgeSourceUrl } from './api';
import type {
  KnowledgeAdminDocument,
  KnowledgeCatalog,
  KnowledgeSourceDocument,
  RagDocument,
  RagLibrary,
} from './types';

const libraryIcons = {
  policy: IconBuildingBank,
  public_reference: IconWorld,
  case_history: IconHistory,
  proposed_rule: IconShieldCheck,
  evidence_spec: IconFileText,
};

const markdownRenderer = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

const roleLabels: Record<string, string> = {
  credit_analyst: '信用分析师',
  senior_credit_analyst: '高级信用分析师',
  admin: '系统管理员',
  super_admin: '超级管理员',
};

function formatDocumentDate(value: string) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date);
}

type ManagerMode = 'upload' | 'edit' | null;
type BrowsableDocument = RagDocument & {
  libraryId: RagLibrary['id'];
  libraryLabel: string;
};

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
      className={`group relative w-full overflow-hidden rounded-md px-3 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none ${
        active
          ? 'bg-white shadow-sm ring-1 ring-slate-200 before:absolute before:inset-y-2 before:left-0 before:w-0.5 before:rounded-r before:bg-[#2457D6]'
          : 'hover:bg-white/80'
      }`}
    >
      <div className="flex items-center gap-3">
        <span
          className={`grid size-8 shrink-0 place-items-center rounded-md ${
            active
              ? 'bg-[#2457D6] text-white'
              : library.accessible
                ? 'bg-white text-slate-600 ring-1 ring-slate-200 group-hover:text-blue-700'
                : 'bg-slate-100 text-slate-400'
          }`}
        >
          <Icon size={17} stroke={1.8} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center justify-between gap-2">
            <strong className="truncate text-[13px] font-semibold text-slate-900">
              {library.label}
            </strong>
            {!library.accessible && (
              <IconLock size={14} className="shrink-0 text-slate-400" />
            )}
          </span>
          <span className="mt-0.5 block text-xs leading-4 text-slate-500 tabular-nums">
            {library.document_count} 份文档
          </span>
        </span>
      </div>
    </button>
  );
}

function DocumentNavItem({
  document,
  libraryLabel,
  showLibrary,
  active,
  onSelect,
}: {
  document: RagDocument;
  libraryLabel: string;
  showLibrary: boolean;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`relative w-full overflow-hidden rounded-md px-3 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none ${
        active
          ? 'bg-[#E9F0FF] text-blue-950 ring-1 ring-blue-200 ring-inset before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-r before:bg-[#2457D6]'
          : 'hover:bg-slate-100'
      }`}
    >
      <span className="flex items-start gap-3">
        <IconFileText
          size={17}
          stroke={1.7}
          className={`mt-0.5 shrink-0 ${active ? 'text-blue-700' : 'text-slate-400'}`}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-start justify-between gap-2">
            <strong
              className={`line-clamp-2 block text-[12px] leading-5 font-semibold break-words ${
                active ? 'text-[#173E9C]' : 'text-slate-900'
              }`}
            >
              {document.title}
            </strong>
            {active && (
              <span className="mt-0.5 shrink-0 rounded bg-white/80 px-1.5 py-0.5 text-xs leading-4 font-semibold text-[#2457D6] ring-1 ring-blue-200">
                正在阅读
              </span>
            )}
          </span>
          <span className="mt-1 flex items-center gap-1.5 text-xs leading-4 text-slate-500 tabular-nums">
            {showLibrary && (
              <>
                <span className="shrink-0 font-medium text-slate-600">
                  {libraryLabel}
                </span>
                <span aria-hidden="true">·</span>
              </>
            )}
            <span className="truncate">
              {document.source_filename || '系统文档'}
            </span>
            {formatDocumentDate(document.updated_at) && (
              <>
                <span aria-hidden="true">·</span>
                <span className="shrink-0">
                  {formatDocumentDate(document.updated_at)}
                </span>
              </>
            )}
          </span>
        </span>
      </span>
    </button>
  );
}

function DocumentReader({
  document,
  source,
  loading,
  error,
  onBack,
}: {
  document: RagDocument;
  source: KnowledgeSourceDocument | null;
  loading: boolean;
  error: string;
  onBack: () => void;
}) {
  const [pdfPage, setPdfPage] = useState(1);
  const [pdfFit, setPdfFit] = useState<'page' | 'width'>('page');
  const [previewError, setPreviewError] = useState(false);
  const sourceUrl = knowledgeSourceUrl(document.id);
  const isPdf =
    document.mime_type === 'application/pdf' && document.has_original;
  const isImage =
    document.mime_type.startsWith('image/') && document.has_original;
  const renderedSource = useMemo(
    () => markdownRenderer.render(source?.source_content || ''),
    [source?.source_content]
  );
  const previewPageCount = Math.max(source?.preview_page_count || 1, 1);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col bg-[#EEF2F6]">
      <header className="flex h-16 shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-3 md:gap-3 md:px-5">
        <button
          type="button"
          onClick={onBack}
          className="grid size-8 shrink-0 place-items-center rounded-md text-slate-600 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none md:hidden"
          aria-label="返回文档列表"
        >
          <IconArrowLeft size={17} />
        </button>
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-md bg-[#EEF3FB] text-[#2457D6]">
            <IconFileText size={17} stroke={1.8} />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-[13px] leading-5 font-semibold text-slate-950">
              {document.title}
            </h2>
            <p className="mt-0.5 truncate text-xs leading-4 text-slate-500">
              {document.source_filename || '系统文档'}
              {formatDocumentDate(document.updated_at)
                ? ` · 更新于 ${formatDocumentDate(document.updated_at)}`
                : ''}
            </p>
          </div>
        </div>
        <Button asChild variant="outline" size="sm" className="shrink-0">
          <a
            href={sourceUrl}
            target="_blank"
            rel="noreferrer"
            aria-label="查看原文件"
          >
            <IconExternalLink />
            <span className="hidden sm:inline">查看原文件</span>
          </a>
        </Button>
      </header>
      {loading ? (
        <div className="flex-1 overflow-hidden p-5 md:p-8">
          <div className="mx-auto max-w-[820px] space-y-5 bg-white px-8 py-10 shadow-[0_14px_34px_-26px_rgba(15,23,42,.5)] md:px-14 md:py-12">
            <Skeleton className="h-7 w-1/2 bg-slate-200" />
            <Skeleton className="h-4 w-full bg-slate-200" />
            <Skeleton className="h-4 w-11/12 bg-slate-200" />
            <Skeleton className="h-4 w-4/5 bg-slate-200" />
          </div>
        </div>
      ) : error ? (
        <div className="m-5 flex items-start gap-3 rounded-lg bg-red-50 p-4 text-sm text-red-950 ring-1 ring-red-200 md:m-7">
          <IconAlertTriangle size={18} className="mt-0.5 shrink-0" />
          <p>{error}</p>
        </div>
      ) : isPdf ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex min-h-11 shrink-0 items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 md:px-5">
            <span className="font-medium text-slate-700">PDF 预览</span>
            <div className="flex items-center gap-1.5">
              <div
                className="mr-1 flex items-center rounded-md bg-slate-100 p-0.5"
                aria-label="PDF 显示方式"
              >
                <button
                  type="button"
                  onClick={() => setPdfFit('page')}
                  aria-pressed={pdfFit === 'page'}
                  className={`rounded px-2 py-1 font-medium transition-colors focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none ${
                    pdfFit === 'page'
                      ? 'bg-white text-blue-700 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  整页
                </button>
                <button
                  type="button"
                  onClick={() => setPdfFit('width')}
                  aria-pressed={pdfFit === 'width'}
                  className={`rounded px-2 py-1 font-medium transition-colors focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none ${
                    pdfFit === 'width'
                      ? 'bg-white text-blue-700 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  宽度
                </button>
              </div>
              <button
                type="button"
                onClick={() => {
                  setPreviewError(false);
                  setPdfPage((value) => Math.max(1, value - 1));
                }}
                disabled={pdfPage <= 1}
                className="grid size-7 place-items-center rounded-md text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="上一页"
              >
                <IconChevronLeft size={16} />
              </button>
              <span className="min-w-16 text-center tabular-nums">
                第 {pdfPage} / {previewPageCount} 页
              </span>
              <button
                type="button"
                onClick={() => {
                  setPreviewError(false);
                  setPdfPage((value) => Math.min(previewPageCount, value + 1));
                }}
                disabled={pdfPage >= previewPageCount}
                className="grid size-7 place-items-center rounded-md text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-35"
                aria-label="下一页"
              >
                <IconChevronRight size={16} />
              </button>
            </div>
          </div>
          <div
            data-testid="pdf-preview-canvas"
            className={`relative min-h-0 flex-1 bg-[#E9EEF4] p-3 md:p-5 ${
              pdfFit === 'page' ? 'overflow-hidden' : 'overflow-auto'
            }`}
          >
            {previewError ? (
              <div className="mx-auto flex min-h-64 max-w-lg items-center justify-center rounded-lg bg-white p-7 text-center shadow-sm">
                <div>
                  <IconAlertTriangle
                    size={22}
                    className="mx-auto text-amber-600"
                  />
                  <p className="mt-3 text-sm font-semibold text-slate-900">
                    PDF 页面预览失败
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    可使用右上角“查看原文件”在浏览器中打开完整 PDF。
                  </p>
                </div>
              </div>
            ) : (
              <div
                className={
                  pdfFit === 'page'
                    ? 'absolute inset-3 flex items-center justify-center md:inset-5'
                    : ''
                }
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={knowledgePreviewUrl(document.id, pdfPage)}
                  alt={`${document.title} 第 ${pdfPage} 页`}
                  onError={() => setPreviewError(true)}
                  className={`bg-white shadow-[0_14px_34px_-24px_rgba(15,23,42,.45)] ${
                    pdfFit === 'page'
                      ? 'block max-h-full max-w-full object-contain'
                      : 'mx-auto h-auto w-full max-w-[940px]'
                  }`}
                />
              </div>
            )}
          </div>
        </div>
      ) : isImage ? (
        <div className="grid min-h-[560px] flex-1 place-items-center overflow-auto p-5 md:p-8">
          {previewError ? (
            <div className="rounded-lg bg-white p-7 text-center shadow-sm">
              <IconAlertTriangle size={22} className="mx-auto text-amber-600" />
              <p className="mt-3 text-sm font-semibold text-slate-900">
                图片预览失败
              </p>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                可使用右上角“查看原文件”打开完整图片。
              </p>
            </div>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={knowledgePreviewUrl(document.id)}
              alt={document.title}
              onError={() => setPreviewError(true)}
              className="max-h-[720px] max-w-full bg-white object-contain shadow-[0_16px_36px_-24px_rgba(15,23,42,.45)]"
            />
          )}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-5 sm:px-5 md:px-8 md:py-7">
          <article className="mx-auto min-h-full max-w-[820px] bg-white px-6 py-9 shadow-[0_14px_34px_-26px_rgba(15,23,42,.5)] sm:px-9 md:px-14 md:py-12">
            {source?.source_content ? (
              <div
                className="max-w-[72ch] text-[14px] leading-7 text-slate-700 selection:bg-blue-100 selection:text-blue-950 [&_a]:font-medium [&_a]:text-blue-700 [&_a]:underline [&_a]:decoration-blue-200 [&_a]:underline-offset-4 [&_blockquote]:my-6 [&_blockquote]:border-l [&_blockquote]:border-slate-300 [&_blockquote]:pl-4 [&_blockquote]:text-slate-600 [&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[13px] [&_h1]:mb-7 [&_h1]:text-2xl [&_h1]:leading-9 [&_h1]:font-semibold [&_h1]:tracking-[-0.025em] [&_h1]:text-slate-950 [&_h2]:mt-10 [&_h2]:mb-4 [&_h2]:text-lg [&_h2]:leading-7 [&_h2]:font-semibold [&_h2]:tracking-[-0.015em] [&_h2]:text-slate-950 [&_h3]:mt-8 [&_h3]:mb-3 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:text-slate-900 [&_hr]:my-8 [&_hr]:border-slate-200 [&_li]:my-1.5 [&_ol]:my-4 [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:my-4 [&_pre]:my-5 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-slate-950 [&_pre]:p-4 [&_pre]:text-slate-100 [&_strong]:font-semibold [&_strong]:text-slate-950 [&_table]:my-6 [&_table]:w-full [&_table]:border-collapse [&_td]:border-b [&_td]:border-slate-200 [&_td]:px-3 [&_td]:py-2.5 [&_td]:align-top [&_th]:border-b [&_th]:border-slate-300 [&_th]:bg-slate-50 [&_th]:px-3 [&_th]:py-2.5 [&_th]:text-left [&_th]:text-xs [&_th]:font-semibold [&_th]:text-slate-700 [&_ul]:my-4 [&_ul]:list-disc [&_ul]:pl-6"
                dangerouslySetInnerHTML={{ __html: renderedSource }}
              />
            ) : (
              <div className="grid min-h-72 place-items-center text-center">
                <div>
                  <IconFileText size={24} className="mx-auto text-slate-400" />
                  <p className="mt-3 text-sm font-medium text-slate-800">
                    该文档暂无可显示的正文
                  </p>
                </div>
              </div>
            )}
          </article>
        </div>
      )}
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

  if (confirmDelete && document) {
    const libraryLabel =
      libraries.find((library) => library.id === document.library_id)?.label ||
      document.library_id;
    return (
      <section className="p-5 md:p-7" aria-labelledby="delete-document-title">
        <div className="flex flex-col gap-4 border-b border-slate-200 pb-5 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2
              id="delete-document-title"
              className="text-lg font-semibold tracking-[-0.02em] text-slate-950"
            >
              删除知识文档
            </h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              请确认当前选择。只有管理员能够执行此操作。
            </p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
            <IconX /> 关闭维护
          </Button>
        </div>

        <dl className="mt-6 grid gap-x-8 gap-y-5 rounded-xl bg-slate-50 p-5 ring-1 ring-slate-200 sm:grid-cols-2">
          <div className="min-w-0 sm:col-span-2">
            <dt className="text-xs font-medium text-slate-500">文档</dt>
            <dd className="mt-1 text-sm font-semibold break-words text-slate-950">
              {document.title}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs font-medium text-slate-500">资料空间</dt>
            <dd className="mt-1 text-sm text-slate-800">{libraryLabel}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs font-medium text-slate-500">检索分段</dt>
            <dd className="mt-1 text-sm text-slate-800 tabular-nums">
              {document.chunk_count} 个 Chunk
            </dd>
          </div>
          <div className="min-w-0 sm:col-span-2">
            <dt className="text-xs font-medium text-slate-500">原文件</dt>
            <dd className="mt-1 truncate text-sm text-slate-800">
              {document.source_filename || '无独立原文件'}
            </dd>
          </div>
        </dl>

        <div
          role="alert"
          className="mt-5 flex items-start gap-3 rounded-xl bg-red-50 p-4 text-red-950 ring-1 ring-red-200"
        >
          <IconAlertTriangle size={18} className="mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold">
              原文件、数据库正文及全部检索分段都会删除
            </p>
            <p className="mt-1 text-xs leading-5 text-red-800">
              删除后 Agent
              将不再检索到该文档。删除人、时间和文档摘要会保留在数据库审计记录中。
            </p>
          </div>
        </div>

        {error && (
          <div
            role="alert"
            className="mt-5 flex items-start gap-3 rounded-lg bg-red-50 p-4 text-xs leading-5 text-red-950 ring-1 ring-red-200"
          >
            <IconAlertTriangle size={17} className="mt-0.5 shrink-0" />
            <p>{error}</p>
          </div>
        )}

        <div className="mt-6 flex flex-col-reverse gap-3 border-t border-slate-200 pt-5 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="outline"
            disabled={saving}
            onClick={onCancelDelete}
          >
            返回编辑
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={saving}
            onClick={onDelete}
          >
            {saving ? <IconLoader2 className="animate-spin" /> : <IconTrash />}
            {saving ? '正在删除' : '确认删除文档'}
          </Button>
        </div>
      </section>
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
              ? '支持图片、Markdown、PDF、TXT、DOC 和 DOCX。上传后先解析完整正文，再在后台建立检索索引。'
              : '这里编辑的是完整解析正文。保存后会更新源内容并在后台重建检索索引。'}
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
            accept=".png,.jpg,.jpeg,.md,.txt,.pdf,.doc,.docx,image/png,image/jpeg,text/markdown,text/plain,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            required
            disabled={saving}
            onChange={(event) => onFileChange(event.target.files?.[0] || null)}
            className="mt-2 h-auto min-h-11 cursor-pointer bg-white py-2 file:mr-3 file:cursor-pointer"
          />
          <span className="mt-1.5 block text-[11px] leading-5 text-slate-500">
            单个文件不超过 10 MB；图片和扫描 PDF 会使用已配置的 OCR 解析。
            {file ? ` 已选择：${file.name}` : ''}
          </span>
        </label>
      ) : (
        <label className="mt-5 block">
          <span className="text-xs font-semibold text-slate-800">
            完整解析正文
          </span>
          <Textarea
            value={content}
            onChange={(event) => onContentChange(event.target.value)}
            required
            disabled={saving}
            maxLength={2_000_000}
            className="mt-2 min-h-[360px] resize-y bg-white font-sans text-base leading-7 md:text-sm"
            placeholder="这是用户阅读和 RAG 建索引共同使用的完整正文。"
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

      <div className="mt-6 flex flex-col-reverse gap-3 border-t border-slate-200 pt-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          {mode === 'edit' && (
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
          <Button type="submit" disabled={saving}>
            {saving ? (
              <IconLoader2 className="animate-spin" />
            ) : mode === 'upload' ? (
              <IconUpload />
            ) : (
              <IconCheck />
            )}
            {saving
              ? '正在解析并建索引'
              : mode === 'upload'
                ? '上传并解析'
                : '保存并重建索引'}
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
  const [sourceDocument, setSourceDocument] =
    useState<KnowledgeSourceDocument | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState('');
  const [mobileView, setMobileView] = useState<
    'libraries' | 'documents' | 'reader'
  >('documents');

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
  const allDocuments = useMemo<BrowsableDocument[]>(
    () =>
      (catalog?.libraries || []).flatMap((item) =>
        item.accessible
          ? item.documents.map((document) => ({
              ...document,
              libraryId: item.id,
              libraryLabel: item.label,
            }))
          : []
      ),
    [catalog?.libraries]
  );
  const documents = useMemo(() => {
    if (library && !library.accessible) return [];
    const keyword = query.trim().toLocaleLowerCase();
    const scopedDocuments = keyword
      ? allDocuments
      : allDocuments.filter((item) => item.libraryId === libraryId);
    if (!keyword) return scopedDocuments;
    return scopedDocuments.filter((document) =>
      [document.title, document.citation, document.source_filename]
        .join(' ')
        .toLocaleLowerCase()
        .includes(keyword)
    );
  }, [allDocuments, library, libraryId, query]);
  const document =
    documents.find((item) => item.id === documentId) || documents[0] || null;

  useEffect(() => {
    if (!document || managerMode) {
      setSourceDocument(null);
      setSourceError('');
      return;
    }
    let cancelled = false;
    setSourceLoading(true);
    setSourceError('');
    craApi
      .knowledgeDocument(document.id)
      .then((result) => {
        if (!cancelled) setSourceDocument(result);
      })
      .catch((reason) => {
        if (!cancelled) {
          setSourceError(
            reason instanceof Error ? reason.message : '无法读取源文件正文'
          );
        }
      })
      .finally(() => {
        if (!cancelled) setSourceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [document, managerMode]);

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
    setMobileView('documents');
  };

  const openUpload = () => {
    setManagerMode('upload');
    setDraftLibraryId(
      library?.accessible && !library.read_only
        ? library.id
        : catalog?.libraries.find((item) => item.accessible && !item.read_only)?.id ||
            'policy'
    );
    setDraftTitle('');
    setDraftContent('');
    setDraftFile(null);
    setAdminDocument(null);
    setManagerError('');
    setConfirmDelete(false);
    setMobileView('reader');
  };

  const openDocumentManager = async (requestDelete: boolean) => {
    if (!document) return;
    setMobileView('reader');
    setManagerMode('edit');
    setManagerLoading(true);
    setManagerError('');
    setConfirmDelete(requestDelete);
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

  const openEdit = () => {
    void openDocumentManager(false);
  };

  const openDelete = () => {
    void openDocumentManager(true);
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
      setManagerError(
        '请选择需要上传的图片、Markdown、PDF、TXT 或 Word 文件。'
      );
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
          ? '源文件已解析，检索索引已建立'
          : '完整正文已保存，检索索引已重建'
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
      toast.success('知识文档已删除，操作已写入审计记录');
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
      <div className="mx-auto max-w-[1680px] px-3 py-4 md:px-5 md:py-5">
        <div className="flex min-h-14 items-center justify-between gap-4">
          <div className="space-y-2">
            <Skeleton className="h-5 w-20 bg-slate-200" />
            <Skeleton className="h-3 w-48 bg-slate-200" />
          </div>
          <Skeleton className="hidden h-4 w-44 bg-slate-200 sm:block" />
        </div>
        <div className="mt-3 flex justify-end">
          <Skeleton className="h-10 w-full rounded-lg bg-slate-200 sm:max-w-[420px]" />
        </div>
        <div className="mt-3 grid min-h-[600px] overflow-hidden rounded-xl bg-white shadow-[0_16px_42px_-32px_rgba(15,23,42,.55)] ring-1 ring-[#D9E0E9] md:grid-cols-[300px_minmax(0,1fr)] lg:h-[calc(100vh-13.5rem)] lg:grid-cols-[220px_300px_minmax(0,1fr)]">
          <div className="hidden bg-slate-50 p-4 lg:block">
            <Skeleton className="h-3 w-20 bg-slate-200" />
            <div className="mt-8 space-y-4">
              {[0, 1, 2].map((item) => (
                <div key={item} className="flex items-center gap-3 px-2">
                  <Skeleton className="size-8 shrink-0 rounded-md bg-slate-200" />
                  <div className="min-w-0 flex-1 space-y-2">
                    <Skeleton className="h-3 w-24 bg-slate-200" />
                    <Skeleton className="h-3 w-16 bg-slate-200" />
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="p-4">
            <Skeleton className="h-4 w-24 bg-slate-200" />
            <Skeleton className="mt-2 h-3 w-52 bg-slate-200" />
            <div className="mt-8 space-y-5">
              {[0, 1, 2, 3].map((item) => (
                <div key={item} className="flex gap-3 px-1">
                  <Skeleton className="mt-0.5 size-4 shrink-0 bg-slate-200" />
                  <div className="min-w-0 flex-1 space-y-2.5">
                    <Skeleton className="h-3.5 w-full bg-slate-200" />
                    <Skeleton className="h-3 w-4/5 bg-slate-200" />
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="hidden min-w-0 bg-[#EEF2F6] md:block">
            <div className="flex h-16 items-center gap-3 bg-white px-5">
              <Skeleton className="size-8 shrink-0 rounded-md bg-slate-200" />
              <div className="min-w-0 flex-1 space-y-2">
                <Skeleton className="h-3.5 w-2/5 bg-slate-200" />
                <Skeleton className="h-3 w-1/4 bg-slate-200" />
              </div>
            </div>
            <div className="p-7">
              <div className="mx-auto max-w-[820px] bg-white px-14 py-12 shadow-[0_14px_34px_-26px_rgba(15,23,42,.5)]">
                <Skeleton className="h-7 w-3/5 bg-slate-200" />
                <div className="mt-10 space-y-3">
                  <Skeleton className="h-4 w-1/4 bg-slate-200" />
                  <Skeleton className="h-3.5 w-full bg-slate-200" />
                  <Skeleton className="h-3.5 w-11/12 bg-slate-200" />
                  <Skeleton className="h-3.5 w-4/5 bg-slate-200" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (error || !catalog) {
    return (
      <div className="mx-auto max-w-[1680px] px-4 py-9 md:px-5">
        <div className="flex min-h-64 items-center justify-center rounded-xl bg-white p-8 text-center ring-1 ring-slate-200">
          <div>
            <IconAlertTriangle size={28} className="mx-auto text-red-600" />
            <h1 className="mt-3 text-base font-semibold">无法读取资料库</h1>
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
    <div className="mx-auto max-w-[1680px] px-3 py-4 md:px-5 md:py-5">
      <header className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
        <div className="flex min-w-0 items-center gap-3.5">
          <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-[#173F9F] text-white shadow-[0_10px_24px_-16px_rgba(23,63,159,.75)]">
            <IconBooks size={21} stroke={1.7} />
          </span>
          <div className="min-w-0">
            <h1 className="text-2xl leading-7 font-semibold tracking-[-0.03em] text-slate-950">
              资料库
            </h1>
            <p className="mt-1.5 text-[13px] leading-5 text-slate-500">
              查阅制度、公开资料与历史案件原文
            </p>
          </div>
        </div>
        <div className="flex flex-col items-start gap-2.5 sm:items-end">
          <div className="flex flex-wrap items-center gap-2.5 text-xs">
            <span className="inline-flex items-center gap-1.5 font-medium text-slate-700">
              <IconShieldCheck size={15} className="text-emerald-600" />
              {roleLabels[catalog.principal.role] || catalog.principal.role}
            </span>
            <span className="h-4 w-px bg-slate-200" aria-hidden="true" />
            <span className="text-slate-500 tabular-nums">
              已授权{' '}
              <strong className="font-semibold text-slate-800">
                {catalog.granted_library_count}/{catalog.libraries.length}
              </strong>{' '}
              个资料空间
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {catalog.can_manage && document && !document.read_only && !managerMode && !(
              document.libraryId === 'proposed_rule' || document.libraryId === 'evidence_spec'
            ) && (
              <>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={openEdit}
                >
                  <IconEdit /> 编辑文档
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="text-red-700 hover:border-red-200 hover:bg-red-50 hover:text-red-800"
                  onClick={openDelete}
                >
                  <IconTrash /> 删除文档
                </Button>
              </>
            )}
            {catalog.can_manage && (
              <Button type="button" size="sm" onClick={openUpload}>
                <IconUpload /> 上传文档
              </Button>
            )}
          </div>
        </div>
      </header>

      <div className="mt-4 flex h-12 overflow-hidden rounded-xl bg-white shadow-[0_12px_30px_-24px_rgba(15,23,42,.65)] ring-1 ring-[#D9E0E9] transition-[box-shadow] focus-within:shadow-[0_14px_34px_-24px_rgba(36,87,214,.5)] focus-within:ring-2 focus-within:ring-[#2457D6]">
        <span className="grid w-12 shrink-0 place-items-center border-r border-slate-200 bg-slate-50 text-[#2457D6]">
          <IconSearch size={17} stroke={1.8} />
        </span>
        <label className="flex min-w-0 flex-1 items-center px-3.5 text-sm text-slate-600">
          <span className="sr-only">搜索全部已授权文件</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索全部已授权文件"
            className="h-full min-w-0 flex-1 bg-transparent text-slate-900 outline-none placeholder:text-slate-500"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              className="grid size-6 shrink-0 place-items-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none"
              aria-label="清除搜索"
            >
              <IconX size={14} />
            </button>
          )}
        </label>
        <span className="hidden shrink-0 items-center gap-2 border-l border-slate-200 px-4 text-xs text-slate-500 md:flex">
          <IconFileText size={15} stroke={1.7} />
          <span className="tabular-nums">
            {query
              ? `${documents.length} 个匹配结果`
              : `${allDocuments.length} 份文件`}
          </span>
        </span>
      </div>

      <section
        className="mt-3 overflow-hidden rounded-xl bg-white shadow-[0_16px_42px_-32px_rgba(15,23,42,.55)] ring-1 ring-[#D9E0E9] lg:h-[calc(100vh-14.75rem)] lg:min-h-[500px]"
        aria-label="资料内容"
      >
        <div className="grid h-full min-h-[640px] lg:min-h-0 lg:grid-cols-[220px_minmax(0,1fr)]">
          <aside
            className={`${mobileView === 'libraries' ? 'flex' : 'hidden'} min-h-0 flex-col bg-slate-50 lg:flex lg:border-r lg:border-slate-200`}
          >
            <div className="flex h-16 shrink-0 items-center border-b border-slate-200 px-4">
              <h2 className="text-sm font-semibold tracking-[-0.01em] text-slate-800">
                资料分类
              </h2>
            </div>
            <div className="space-y-1 overflow-y-auto p-2.5">
              {catalog.libraries.map((item) => (
                <LibraryNavItem
                  key={item.id}
                  library={item}
                  active={item.id === libraryId}
                  onSelect={() => selectLibrary(item)}
                />
              ))}
            </div>
            <div className="mt-auto hidden shrink-0 px-4 py-3 text-xs leading-4 text-slate-500 lg:block">
              访问权限由系统按角色控制
            </div>
          </aside>

          {!library?.accessible ? (
            <div className="grid min-h-[520px] place-items-center px-6 py-12 text-center lg:min-h-0">
              <div className="max-w-md">
                <span className="mx-auto grid size-12 place-items-center rounded-xl bg-slate-100 text-slate-400">
                  <IconLock size={23} />
                </span>
                <h2 className="mt-4 text-base font-semibold text-slate-950">
                  {library?.label}尚未授权
                </h2>
                <p className="mt-2 text-sm leading-6 text-slate-600">
                  该库包含 {library?.document_count} 份源文件。需要
                  {library?.required_role}，可联系区域信用经理申请。
                </p>
                <p className="mt-3 text-xs leading-5 text-slate-500">
                  未授权正文不会发送到当前浏览器，也不会供本账号检索。
                </p>
              </div>
            </div>
          ) : (
            <div className="grid min-h-0 min-w-0 md:grid-cols-[300px_minmax(0,1fr)]">
              <div
                className={`${mobileView === 'documents' ? 'flex' : 'hidden'} min-h-0 flex-col bg-white md:flex md:border-r md:border-slate-200`}
              >
                <header className="flex h-16 shrink-0 items-center gap-2 border-b border-slate-200 px-3 md:px-4">
                  <button
                    type="button"
                    onClick={() => setMobileView('libraries')}
                    className="grid size-8 shrink-0 place-items-center rounded-md text-slate-600 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none md:hidden"
                    aria-label="返回资料分类"
                  >
                    <IconArrowLeft size={17} />
                  </button>
                  <div className="min-w-0">
                    <h2 className="truncate text-[13px] font-semibold text-slate-950">
                      {query ? '搜索结果' : library.label}
                    </h2>
                    <p className="mt-0.5 truncate text-xs leading-4 text-slate-500">
                      {query
                        ? `搜索全部已授权资料 · ${documents.length} 个结果`
                        : library.description}
                    </p>
                  </div>
                </header>
                {documents.length ? (
                  <nav
                    className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2.5 pb-2.5"
                    aria-label={
                      query ? '全部资料搜索结果' : `${library.label}文档`
                    }
                  >
                    {documents.map((item) => (
                      <DocumentNavItem
                        key={item.id}
                        document={item}
                        libraryLabel={item.libraryLabel}
                        showLibrary={Boolean(query)}
                        active={item.id === document?.id}
                        onSelect={() => {
                          setDocumentId(item.id);
                          closeManager();
                          setMobileView('reader');
                        }}
                      />
                    ))}
                  </nav>
                ) : (
                  <div className="grid min-h-72 flex-1 place-items-center px-6 text-center">
                    <div>
                      <IconSearch
                        size={22}
                        className="mx-auto text-slate-400"
                      />
                      <h3 className="mt-3 text-sm font-semibold text-slate-900">
                        {query ? '没有匹配内容' : '当前分类尚无文档'}
                      </h3>
                      <p className="mt-2 text-xs leading-5 text-slate-500">
                        {query
                          ? '请尝试搜索制度名称、评分字段或风险主题。'
                          : catalog.can_manage
                            ? '上传文档后，系统会解析正文并建立检索索引。'
                            : '请联系管理员维护该资料分类。'}
                      </p>
                    </div>
                  </div>
                )}
              </div>

              <div
                className={`${mobileView === 'reader' ? 'block' : 'hidden'} h-full min-h-0 min-w-0 md:block`}
              >
                {managerMode ? (
                  <KnowledgeManager
                    mode={managerMode}
                    libraries={catalog.libraries.filter(
                      (item) => item.accessible && !item.read_only
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
                  document && (
                    <DocumentReader
                      key={document.id}
                      document={document}
                      source={sourceDocument}
                      loading={sourceLoading}
                      error={sourceError}
                      onBack={() => setMobileView('documents')}
                    />
                  )
                )}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
