'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  IconAlertTriangle,
  IconArrowRight,
  IconBolt,
  IconClock,
  IconFilePlus,
  IconRefresh,
  IconUsers,
} from '@tabler/icons-react';

import { Button } from '@/shared/components/ui/button';
import { Skeleton } from '@/shared/components/ui/skeleton';

import { craApi } from './api';
import {
  dateTime,
  money,
  stageLabels,
  statusLabels,
  statusTone,
} from './presentation';
import type { CraCaseSummary } from './types';

function CaseRow({ item }: { item: CraCaseSummary }) {
  const wf = item.workflow;
  const progress = Math.min(
    100,
    Math.round((wf.stage_index / Math.max(wf.total_stages, 1)) * 100)
  );
  return (
    <Link
      href={`/workspace/cases/${item.case_id}`}
      className="group grid gap-4 border-b border-slate-200 px-5 py-5 transition-colors last:border-0 hover:bg-slate-50 md:grid-cols-[1.45fr_.8fr_.9fr_.5fr_24px] md:items-center md:px-6"
    >
      <div className="min-w-0">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <h3 className="truncate text-sm font-semibold text-slate-950">
            {item.title}
          </h3>
          <span
            className={`rounded-md px-2 py-1 text-[11px] font-semibold ${statusTone[wf.status]}`}
          >
            {statusLabels[wf.status]}
          </span>
        </div>
        <p className="truncate text-xs text-slate-500">
          {item.case_id} · BP {item.bp} ·{' '}
          {item.customer.country || '国家待补充'}
        </p>
      </div>
      <div>
        <p className="text-xs text-slate-500">申请额度</p>
        <p className="mt-1 text-sm font-semibold text-slate-900 tabular-nums">
          {money(item.requested_cl_usd)}
        </p>
      </div>
      <div>
        <div className="mb-2 flex items-center justify-between text-[11px]">
          <span className="font-medium text-slate-700">
            {stageLabels[wf.current_stage] || wf.current_stage}
          </span>
          <span className="text-slate-400 tabular-nums">{progress}%</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full bg-blue-600"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
      <div className="text-xs text-slate-500">
        <span className="md:hidden">更新于 </span>
        {dateTime(wf.updated_at)}
      </div>
      <IconArrowRight
        size={17}
        className="hidden text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-blue-600 md:block"
      />
    </Link>
  );
}

export function CaseList() {
  const [cases, setCases] = useState<CraCaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setCases(await craApi.cases());
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : '无法连接 Agent 服务'
      );
    }
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    craApi
      .cases()
      .then((items) => {
        if (!cancelled) setCases(items);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : '无法连接 Agent 服务'
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const counts = useMemo(
    () => ({
      running: cases.filter((item) => item.workflow.status === 'running')
        .length,
      human: cases.filter((item) => item.workflow.status === 'awaiting_human')
        .length,
      blocked: cases.filter((item) => item.workflow.status === 'blocked')
        .length,
    }),
    [cases]
  );

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-7 md:px-7 md:py-9">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.03em] text-slate-950 md:text-3xl">
            案件工作台
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            今天需要先处理 {counts.human + counts.blocked} 个人工任务或阻断项。
          </p>
        </div>
        <Button
          asChild
          size="lg"
          className="h-11 rounded-xl bg-blue-700 px-5 hover:bg-blue-800"
        >
          <Link href="/workspace/cases/new">
            <IconFilePlus /> 新建案件
          </Link>
        </Button>
      </div>

      <div className="mt-8 grid gap-4 md:grid-cols-3">
        {(
          [
            [
              'Agent 处理中',
              counts.running,
              '正在自动执行可逆任务',
              IconBolt,
              'text-blue-700 bg-blue-50',
            ],
            [
              '等待人工',
              counts.human,
              '企业、证据或结论待签署',
              IconUsers,
              'text-amber-800 bg-amber-50',
            ],
            [
              '已阻断',
              counts.blocked,
              '需要补充材料或处理异常',
              IconAlertTriangle,
              'text-red-700 bg-red-50',
            ],
          ] as const
        ).map(([label, value, detail, Icon, tone]) => (
          <div
            key={String(label)}
            className="flex items-start justify-between rounded-2xl bg-white p-5 shadow-[0_8px_28px_-22px_rgba(15,23,42,.35)] ring-1 ring-slate-200"
          >
            <div>
              <p className="text-sm font-medium text-slate-600">{label}</p>
              <p className="mt-2 text-3xl font-semibold tracking-[-0.03em] text-slate-950 tabular-nums">
                {value}
              </p>
              <p className="mt-2 text-xs leading-5 text-slate-500">{detail}</p>
            </div>
            <span
              className={`grid size-10 place-items-center rounded-xl ${tone}`}
            >
              <Icon size={20} stroke={1.8} />
            </span>
          </div>
        ))}
      </div>

      <section className="mt-7 overflow-hidden rounded-2xl bg-white shadow-[0_8px_28px_-22px_rgba(15,23,42,.35)] ring-1 ring-slate-200">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 md:px-6">
          <div>
            <h2 className="text-sm font-semibold text-slate-950">活动案件</h2>
            <p className="mt-1 text-xs text-slate-500">
              按工作流状态排列，人工待办优先。
            </p>
          </div>
          <button
            onClick={() => void load()}
            className="grid size-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100"
            aria-label="刷新案件"
          >
            <IconRefresh size={18} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        {loading ? (
          <div className="space-y-6 p-6">
            {[1, 2, 3, 4].map((item) => (
              <Skeleton key={item} className="h-14 w-full rounded-xl" />
            ))}
          </div>
        ) : error ? (
          <div className="grid min-h-64 place-items-center p-8 text-center">
            <div>
              <IconAlertTriangle className="mx-auto text-red-500" size={28} />
              <h3 className="mt-3 text-sm font-semibold">无法读取案件</h3>
              <p className="mt-2 text-sm text-slate-500">{error}</p>
              <Button
                variant="outline"
                className="mt-4"
                onClick={() => void load()}
              >
                <IconRefresh /> 重试
              </Button>
            </div>
          </div>
        ) : cases.length ? (
          <div>
            {cases
              .sort((a, b) =>
                a.workflow.status === 'awaiting_human'
                  ? -1
                  : b.workflow.status === 'awaiting_human'
                    ? 1
                    : 0
              )
              .map((item) => (
                <CaseRow key={item.case_id} item={item} />
              ))}
          </div>
        ) : (
          <div className="grid min-h-64 place-items-center p-8 text-center">
            <div>
              <IconClock className="mx-auto text-slate-400" size={28} />
              <h3 className="mt-3 text-sm font-semibold">还没有案件</h3>
              <p className="mt-2 text-sm text-slate-500">
                上传第一份 CRA Excel 开始评审。
              </p>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
