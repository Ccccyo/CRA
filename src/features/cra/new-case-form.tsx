'use client';

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
import { ChangeEvent, DragEvent, useState } from 'react';

import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Label } from '@/shared/components/ui/label';

import { API_URL, craApi } from './api';
import type { UploadPreview } from './types';

export function NewCaseForm() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [owner, setOwner] = useState('张俊');
  const [dueDate, setDueDate] = useState('');
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
      const item = await craApi.create(file, { owner, dueDate, duplicateMode });
      router.push(`/workspace/cases/${item.case_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '建案失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-7 md:px-7 md:py-9">
      <button onClick={() => router.back()} className="mb-5 inline-flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-slate-900">
        <IconArrowLeft size={17} /> 返回案件工作台
      </button>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.03em] md:text-3xl">新建信用评审案件</h1>
          <p className="mt-2 text-sm leading-6 text-slate-600">Agent 从完整的 CRA Excel 中读取案件与 SAP 交接数据，不会修改原文件。</p>
        </div>
        <a href={`${API_URL}/samples/northstar`} className="inline-flex items-center gap-2 text-sm font-semibold text-blue-700 hover:text-blue-900">
          <IconDownload size={18} /> 下载可用的演示 Excel
        </a>
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1.3fr_.7fr]">
        <section className="min-w-0 rounded-2xl bg-white p-5 shadow-[0_8px_28px_-22px_rgba(15,23,42,.35)] ring-1 ring-slate-200 md:p-7">
          <div className="mb-5 flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-xl bg-blue-50 text-blue-700"><IconFileSpreadsheet size={20} /></span>
            <div><h2 className="text-sm font-semibold">上传 CRA Excel</h2><p className="mt-0.5 text-xs text-slate-500">系统先预检，确认后才建案。</p></div>
          </div>

          {!file ? (
            <label
              onDragOver={(event) => event.preventDefault()}
              onDrop={onDrop}
              className="group grid min-h-64 min-w-0 max-w-full cursor-pointer place-items-center overflow-hidden rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center transition hover:border-blue-400 hover:bg-blue-50/40 focus-within:ring-2 focus-within:ring-blue-600 sm:p-8"
            >
              <input type="file" accept=".xlsx" className="sr-only" onChange={(event: ChangeEvent<HTMLInputElement>) => void handleFile(event.target.files?.[0] || null)} />
              <div>
                <span className="mx-auto grid size-12 place-items-center rounded-xl bg-white text-blue-700 shadow-sm ring-1 ring-slate-200"><IconUpload size={22} /></span>
                <p className="mt-4 text-sm font-semibold text-slate-900">拖入文件，或点击选择</p>
                <p className="mt-2 max-w-md break-words text-xs leading-5 text-slate-500">仅支持 .xlsx。需包含 Financial Statements、Executive Summary 和 Header params。</p>
              </div>
            </label>
          ) : (
            <div>
              <div className="flex items-center justify-between rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-emerald-50 text-emerald-700"><IconFileSpreadsheet size={21} /></span>
                  <div className="min-w-0"><p className="truncate text-sm font-semibold">{file.name}</p><p className="mt-1 text-xs text-slate-500">{(file.size / 1024).toFixed(1)} KB</p></div>
                </div>
                <button aria-label="移除文件" onClick={() => { setFile(null); setPreview(null); setError(''); }} className="grid size-9 place-items-center rounded-lg text-slate-500 hover:bg-white hover:text-slate-900"><IconX size={18} /></button>
              </div>
              {busy && !preview ? (
                <div className="mt-5 flex items-center gap-3 rounded-xl bg-blue-50 p-4 text-sm text-blue-800"><IconLoader2 size={19} className="animate-spin" /> Agent 正在检查工作表、年份开关和 SAP 数据…</div>
              ) : preview ? (
                <div className="mt-5 space-y-4">
                  <div className={`flex gap-3 rounded-xl p-4 ${preview.valid ? 'bg-emerald-50 text-emerald-900' : 'bg-red-50 text-red-900'}`}>
                    {preview.valid ? <IconCheck size={20} className="shrink-0" /> : <IconAlertCircle size={20} className="shrink-0" />}
                    <div><p className="text-sm font-semibold">{preview.valid ? '文件可用' : '文件未通过预检'}</p><p className="mt-1 text-xs leading-5 opacity-80">{preview.valid ? '关键字段与年份开关已读取。' : preview.errors.join('；')}</p></div>
                  </div>
                  <dl className="grid gap-px overflow-hidden rounded-xl bg-slate-200 ring-1 ring-slate-200 sm:grid-cols-2">
                    {[
                      ['客户', preview.case.CustomerName || '—'],
                      ['BP', preview.case.BP || '—'],
                      ['信用段', preview.case.CreditSegment || '—'],
                      ['报表币种', preview.case.StatementCurrency || '—'],
                      ['SAP 历史期间', `${preview.sap_periods.length} 期`],
                      ['汇率记录', `${preview.fx_count} 条`],
                    ].map(([label, value]) => <div key={label} className="bg-white px-4 py-3"><dt className="text-[11px] text-slate-500">{label}</dt><dd className="mt-1 truncate text-sm font-medium text-slate-900">{value}</dd></div>)}
                  </dl>
                  {preview.duplicate && (
                    <div className="rounded-xl bg-amber-50 p-4 text-amber-950">
                      <p className="text-sm font-semibold">检测到同 BP 案件</p>
                      <p className="mt-1 text-xs leading-5">{preview.duplicate.case_id} · {preview.duplicate.title}。请明确本次是更新还是新评审周期。</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {[['update', '更新当前案件'], ['cycle', '开始新评审周期']].map(([value, label]) => (
                          <button key={value} onClick={() => setDuplicateMode(value)} className={`rounded-lg px-3 py-2 text-xs font-semibold ${duplicateMode === value ? 'bg-amber-900 text-white' : 'bg-white text-amber-900 ring-1 ring-amber-200'}`}>{label}</button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          )}

          {error && <div role="alert" className="mt-5 flex gap-3 rounded-xl bg-red-50 p-4 text-sm text-red-800"><IconAlertCircle size={19} className="shrink-0" /><span>{error}</span></div>}
        </section>

        <aside className="h-fit min-w-0 rounded-2xl bg-white p-5 shadow-[0_8px_28px_-22px_rgba(15,23,42,.35)] ring-1 ring-slate-200 md:p-6">
          <h2 className="text-sm font-semibold">案件责任</h2>
          <div className="mt-5 space-y-5">
            <div><Label htmlFor="owner">负责分析师</Label><Input id="owner" value={owner} onChange={(event) => setOwner(event.target.value)} className="mt-2 h-11 rounded-xl" /></div>
            <div><Label htmlFor="due">期望完成日期</Label><Input id="due" type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} className="mt-2 h-11 rounded-xl" /></div>
          </div>
          <div className="mt-6 rounded-xl bg-slate-50 p-4">
            <p className="text-xs font-semibold text-slate-800">建案后会发生什么？</p>
            <ol className="mt-3 space-y-3 text-xs leading-5 text-slate-600">
              <li className="flex gap-2"><span className="font-semibold tabular-nums text-blue-700">1.</span> Agent 排序企业主体候选</li>
              <li className="flex gap-2"><span className="font-semibold tabular-nums text-blue-700">2.</span> 你确认后才进入财务证据</li>
              <li className="flex gap-2"><span className="font-semibold tabular-nums text-blue-700">3.</span> 所有输入与操作全程留痕</li>
            </ol>
          </div>
          <Button onClick={() => void create()} disabled={busy || !preview?.valid || (!!preview.duplicate && duplicateMode === 'review')} className="mt-6 h-11 w-full rounded-xl bg-blue-700 hover:bg-blue-800">
            {busy ? <IconLoader2 className="animate-spin" /> : <IconFileSpreadsheet />} 创建并启动 Agent
          </Button>
        </aside>
      </div>
    </div>
  );
}

