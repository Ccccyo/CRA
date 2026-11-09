'use client';

import { ChangeEvent, DragEvent, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  IconAlertCircle,
  IconArrowLeft,
  IconCheck,
  IconDownload,
  IconFileSpreadsheet,
  IconLoader2,
  IconUpload,
  IconX,
} from '@tabler/icons-react';

import { Button } from '@/shared/components/ui/button';

import { API_URL, craApi } from './api';
import type { UploadPreview } from './types';

export function NewCaseForm() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [duplicateMode, setDuplicateMode] = useState('review');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const handleFile = async (next: File | null) => {
    if (!next) return;
    if (!next.name.toLowerCase().endsWith('.xlsx')) {
      setError('文件格式不对，请上传 .xlsx 格式的 CRA Excel。');
      return;
    }
    setFile(next);
    setPreview(null);
    setError('');
    setBusy(true);
    try {
      setPreview(await craApi.preview(next));
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法解析这份 Excel');
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    void handleFile(event.dataTransfer.files?.[0] || null);
  };

  const create = async () => {
    if (!file || !preview?.valid) return;
    setBusy(true);
    setError('');
    try {
      const item = await craApi.create(file, { duplicateMode });
      router.push(`/workspace/cases/${item.case_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '建案失败');
    } finally {
      setBusy(false);
    }
  };

  const checking = busy && !preview;
  const ready = Boolean(preview?.valid);

  return (
    <div className="mx-auto max-w-5xl px-4 py-7 md:px-7 md:py-9">
      <button
        onClick={() => router.back()}
        className="mb-5 inline-flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-slate-900"
      >
        <IconArrowLeft size={17} /> 返回案件工作台
      </button>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.03em] md:text-3xl">
            新建信用评审案件
          </h1>
        </div>
        <a
          href={`${API_URL}/samples/northstar`}
          className="inline-flex items-center gap-2 text-sm font-semibold text-blue-700 hover:text-blue-900"
        >
          <IconDownload size={18} /> 下载可用的演示 Excel
        </a>
      </div>

      <ol className="mt-7 grid grid-cols-2 overflow-hidden rounded-xl bg-white ring-1 ring-slate-200">
        {[
          ['1', '上传并检查', !ready],
          ['2', '确认并创建', ready],
        ].map(([number, label, active]) => (
          <li
            key={String(number)}
            aria-current={active ? 'step' : undefined}
            className={`flex items-center gap-3 px-4 py-3 text-sm font-medium md:px-5 ${active ? 'bg-blue-50 text-blue-800' : 'text-slate-500'}`}
          >
            <span
              className={`grid size-6 place-items-center rounded-full text-xs font-semibold ${active ? 'bg-blue-700 text-white' : 'bg-slate-100 text-slate-500'}`}
            >
              {number}
            </span>
            {label}
          </li>
        ))}
      </ol>

      <div className="mt-5">
        <section className="min-w-0 rounded-2xl bg-white p-5 shadow-[0_8px_28px_-22px_rgba(15,23,42,.35)] ring-1 ring-slate-200 md:p-7">
          <div className="mb-5 flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-xl bg-blue-50 text-blue-700">
              <IconFileSpreadsheet size={20} />
            </span>
            <div>
              <h2 className="text-sm font-semibold">上传 Excel</h2>
            </div>
          </div>

          {!file ? (
            <label
              onDragOver={(event) => event.preventDefault()}
              onDrop={onDrop}
              className="group grid min-h-64 max-w-full min-w-0 cursor-pointer place-items-center overflow-hidden rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center transition focus-within:ring-2 focus-within:ring-blue-600 hover:border-blue-400 hover:bg-blue-50/40 sm:p-8"
            >
              <input
                type="file"
                accept=".xlsx"
                className="sr-only"
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  void handleFile(event.target.files?.[0] || null)
                }
              />
              <div>
                <span className="mx-auto grid size-12 place-items-center rounded-xl bg-white text-blue-700 shadow-sm ring-1 ring-slate-200">
                  <IconUpload size={22} />
                </span>
                <p className="mt-4 text-sm font-semibold text-slate-900">
                  拖入文件，或点击选择
                </p>
                <p className="mt-2 max-w-md text-xs leading-5 break-words text-slate-500">
                  仅支持 .xlsx。模板需包含 Financial Statements 和 Executive
                  Summary。
                </p>
              </div>
            </label>
          ) : (
            <div>
              <div className="flex items-center justify-between rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-emerald-50 text-emerald-700">
                    <IconFileSpreadsheet size={21} />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">
                      {file.name}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {(file.size / 1024).toFixed(1)} KB
                    </p>
                  </div>
                </div>
                <button
                  aria-label="移除文件"
                  onClick={() => {
                    setFile(null);
                    setPreview(null);
                    setError('');
                  }}
                  className="grid size-9 place-items-center rounded-lg text-slate-500 hover:bg-white hover:text-slate-900"
                >
                  <IconX size={18} />
                </button>
              </div>
              {checking ? (
                <div className="mt-5 flex items-center gap-3 rounded-xl bg-blue-50 p-4 text-sm text-blue-800">
                  <IconLoader2 size={19} className="animate-spin" />{' '}
                  正在核对模板、案件信息和历史财务…
                </div>
              ) : preview ? (
                <div className="mt-5 space-y-4">
                  <div
                    className={`flex gap-3 rounded-xl p-4 ${preview.valid ? 'bg-emerald-50 text-emerald-900' : 'bg-red-50 text-red-900'}`}
                  >
                    {preview.valid ? (
                      <IconCheck size={20} className="shrink-0" />
                    ) : (
                      <IconAlertCircle size={20} className="shrink-0" />
                    )}
                    <div>
                      <p className="text-sm font-semibold">
                        {preview.valid ? '文件可用' : '文件未通过预检'}
                      </p>
                      <p className="mt-1 text-xs leading-5 opacity-80">
                        {preview.valid
                          ? '案件信息已读取，请确认后创建案件。'
                          : preview.errors.join('；')}
                      </p>
                    </div>
                  </div>
                  <p className="text-[11px] leading-5 text-slate-500">
                    以下信息只用于确认客户和报表口径，不会改写原 Excel。
                  </p>
                  <dl className="grid gap-px overflow-hidden rounded-xl bg-slate-200 ring-1 ring-slate-200 sm:grid-cols-2">
                    {[
                      ['客户', preview.case.CustomerName || '—'],
                      ['客户编号（BP）', preview.case.BP || '—'],
                      ['本期报表截止日', preview.years[0]?.period_end || '—'],
                      ['财务报表币种', preview.case.StatementCurrency || '—'],
                    ].map(([label, value]) => (
                      <div key={label} className="bg-white px-4 py-3">
                        <dt className="text-[11px] text-slate-500">{label}</dt>
                        <dd className="mt-1 truncate text-sm font-medium text-slate-900">
                          {value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  {preview.duplicate && (
                    <div className="rounded-xl bg-amber-50 p-4 text-amber-950">
                      <p className="text-sm font-semibold">检测到同 BP 案件</p>
                      <p className="mt-1 text-xs leading-5">
                        {preview.duplicate.case_id} · {preview.duplicate.title}
                        。请明确本次是更新还是新评审周期。
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {[
                          ['update', '替换当前草稿并重新核验'],
                          ['cycle', '开始新评审周期'],
                        ].map(([value, label]) => (
                          <button
                            key={value}
                            onClick={() => setDuplicateMode(value)}
                            className={`rounded-lg px-3 py-2 text-xs font-semibold ${duplicateMode === value ? 'bg-amber-900 text-white' : 'bg-white text-amber-900 ring-1 ring-amber-200'}`}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                      {duplicateMode === 'update' && (
                        <p className="mt-3 text-xs leading-5 text-amber-900">
                          将保留历史记录，但清除本轮主体确认及后续签署、评分和交付结果；已完成案件不能覆盖。
                        </p>
                      )}
                    </div>
                  )}
                  {preview.valid && (
                    <Button
                      onClick={() => void create()}
                      disabled={
                        busy ||
                        (!!preview.duplicate && duplicateMode === 'review')
                      }
                      className="h-11 w-full rounded-xl bg-blue-700 hover:bg-blue-800"
                    >
                      {busy ? (
                        <IconLoader2 className="animate-spin" />
                      ) : (
                        <IconCheck />
                      )}{' '}
                      {busy ? '正在创建案件…' : '确认信息并创建案件'}
                    </Button>
                  )}
                </div>
              ) : null}
            </div>
          )}

          {error && (
            <div
              role="alert"
              className="mt-5 flex gap-3 rounded-xl bg-red-50 p-4 text-sm text-red-800"
            >
              <IconAlertCircle size={19} className="shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
