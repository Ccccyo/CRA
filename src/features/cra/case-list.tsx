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
  IconSearch,
  IconUsers,
  IconX,
} from '@tabler/icons-react';

import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Skeleton } from '@/shared/components/ui/skeleton';

import { craApi } from './api';
import {
  dateTime,
  money,
  stageLabels,
  statusLabels,
  statusTone,
} from './presentation';
import type { CraCaseSummary, WorkflowStatus } from './types';

const statusPriority: Record<WorkflowStatus, number> = {
  awaiting_human: 0,
  blocked: 1,
  running: 2,
  ready: 3,
  completed: 4,
};

function sortCases(items: CraCaseSummary[]) {
  return [...items].sort((a, b) => {
    const statusDifference =
      statusPriority[a.workflow.status] - statusPriority[b.workflow.status];
    if (statusDifference !== 0) return statusDifference;
    return (
      Date.parse(b.workflow.updated_at) - Date.parse(a.workflow.updated_at)
    );
  });
}

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

export function CaseList({
  canCreateCases = true,
  readOnlyOverview = false,
}: {
  canCreateCases?: boolean;
  readOnlyOverview?: boolean;
}) {
  const [cases, setCases] = useState<CraCaseSummary[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setCases(await craApi.cases());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法连接案件服务');
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
            reason instanceof Error ? reason.message : '无法连接案件服务'
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

  const activeCases = useMemo(
    () => cases.filter((item) => item.workflow.status !== 'completed'),
    [cases]
  );

  const visibleCases = useMemo(() => {
    const terms = query
      .trim()
      .toLocaleLowerCase('zh-CN')
      .split(/\s+/)
      .filter(Boolean);
    const source = terms.length
      ? cases.filter((item) => {
          const searchable = [
            item.case_id,
            item.title,
            item.bp,
            item.customer.name,
            item.customer.country,
            item.customer.state,
            item.customer.city,
            item.workflow.current_task,
            stageLabels[item.workflow.current_stage],
          ]
            .filter(Boolean)
            .join(' ')
            .toLocaleLowerCase('zh-CN');
          return terms.every((term) => searchable.includes(term));
        })
      : activeCases;
    return sortCases(source);
  }, [activeCases, cases, query]);

  const isSearching = Boolean(query.trim());
  const completedCount = cases.length - activeCases.length;

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
        {canCreateCases ? (
          <Button
            asChild
            size="lg"
            className="h-11 rounded-xl bg-blue-700 px-5 hover:bg-blue-800"
          >
            <Link href="/workspace/cases/new">
              <IconFilePlus /> 新建案件
            </Link>
          </Button>
        ) : readOnlyOverview ? (
          <span className="inline-flex h-9 items-center self-start rounded-lg bg-slate-200/70 px-3 text-xs font-semibold text-slate-600 sm:self-auto">
            全部案件 · 只读
          </span>
        ) : null}
      </div>

      <div className="mt-7 overflow-hidden rounded-2xl bg-white shadow-[0_8px_28px_-22px_rgba(15,23,42,.35)] ring-1 ring-slate-200">
        <div className="grid divide-y divide-slate-200 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
          {(
            [
              [
                '系统处理中',
                counts.running,
                '自动处理中的案件',
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
              className="flex min-w-0 items-center gap-3 px-4 py-3.5"
            >
              <span
                className={`grid size-9 shrink-0 place-items-center rounded-lg ${tone}`}
              >
                <Icon size={18} stroke={1.8} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-slate-600">{label}</p>
                <p className="mt-0.5 truncate text-[11px] text-slate-500">
                  {detail}
                </p>
              </div>
              <p className="text-xl font-semibold tracking-[-0.03em] text-slate-950 tabular-nums">
                {value}
              </p>
            </div>
          ))}
        </div>
      </div>

      <section className="mt-7 overflow-hidden rounded-2xl bg-white shadow-[0_8px_28px_-22px_rgba(15,23,42,.35)] ring-1 ring-slate-200">
        <div className="flex flex-col gap-4 border-b border-slate-200 px-5 py-4 md:px-6 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-slate-950">
              {isSearching ? '案件检索结果' : '活动案件'}
            </h2>
            <p
              className="mt-1 text-xs leading-5 text-slate-500"
              aria-live="polite"
            >
              {isSearching
                ? `在全部 ${cases.length} 个案件中找到 ${visibleCases.length} 个匹配结果。`
                : completedCount
                  ? `仅显示未完成案件，人工待办优先；另有 ${completedCount} 个已完成案件可通过检索打开。`
                  : '仅显示未完成案件，人工待办优先。'}
            </p>
          </div>
          <div className="flex w-full items-center gap-2 lg:w-auto">
            <label className="relative block min-w-0 flex-1 lg:w-[360px] lg:flex-none">
              <span className="sr-only">检索案件</span>
              <IconSearch
                size={17}
                stroke={1.8}
                className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-slate-400"
              />
              <Input
                id="case-search"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="检索案件号、客户名或 BP"
                className="h-10 rounded-lg border-slate-300 bg-white pr-10 pl-10 shadow-none [&::-webkit-search-cancel-button]:appearance-none"
              />
              {query ? (
                <button
                  type="button"
                  onClick={() => setQuery('')}
                  className="absolute top-1/2 right-1.5 grid size-7 -translate-y-1/2 place-items-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none"
                  aria-label="清除案件检索"
                >
                  <IconX size={15} />
                </button>
              ) : null}
            </label>
            <button
              type="button"
              onClick={() => void load()}
              className="grid size-10 shrink-0 place-items-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none"
              aria-label="刷新案件"
            >
              <IconRefresh
                size={18}
                className={loading ? 'animate-spin' : ''}
              />
            </button>
          </div>
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
        ) : visibleCases.length ? (
          <div>
            {visibleCases.map((item) => (
              <CaseRow key={item.case_id} item={item} />
            ))}
          </div>
        ) : (
          <div className="grid min-h-64 place-items-center p-8 text-center">
            <div>
              {isSearching ? (
                <IconSearch className="mx-auto text-slate-400" size={28} />
              ) : (
                <IconClock className="mx-auto text-slate-400" size={28} />
              )}
              <h3 className="mt-3 text-sm font-semibold">
                {isSearching ? '没有匹配的案件' : '当前没有未完成案件'}
              </h3>
              <p className="mt-2 text-sm text-slate-500">
                {isSearching
                  ? '请尝试案件号、客户名称、BP 或国家等关键词。'
                  : cases.length
                    ? '全部案件均已完成，可使用上方检索查找历史案件。'
                    : '上传第一份 CRA Excel 开始评审。'}
              </p>
              {isSearching ? (
                <Button
                  type="button"
                  variant="outline"
                  className="mt-4"
                  onClick={() => setQuery('')}
                >
                  清除检索
                </Button>
              ) : null}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
