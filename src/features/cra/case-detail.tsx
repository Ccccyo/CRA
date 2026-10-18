'use client';

import {
  ChangeEvent,
  DragEvent,
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import Link from 'next/link';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconCheck,
  IconChevronDown,
  IconDatabase,
  IconDeviceFloppy,
  IconDownload,
  IconEye,
  IconFileSpreadsheet,
  IconFileText,
  IconFlask,
  IconHistory,
  IconLoader2,
  IconLock,
  IconPlayerPlay,
  IconRefresh,
  IconRobot,
  IconSend,
  IconShieldCheck,
  IconSparkles,
  IconUpload,
  IconUserCheck,
  IconX,
} from '@tabler/icons-react';
import { toast } from 'sonner';

import { Button } from '@/shared/components/ui/button';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { Textarea } from '@/shared/components/ui/textarea';

import { craApi, financialReportSourceUrl } from './api';
import {
  dateTime,
  money,
  stageLabels,
  statusLabels,
  statusTone,
} from './presentation';
import type { CraCase, HumanTask, StageSnapshot } from './types';

const visibleStages = [
  'resolve_entity',
  'financial_evidence',
  'retrieve_context',
  'risk_signoff',
  'write_workbook',
  'final_signoff',
];

const stageAliases: Record<string, string[]> = {
  resolve_entity: ['resolve_entity'],
  financial_evidence: ['financial_evidence', 'financial_signoff'],
  retrieve_context: ['retrieve_context'],
  risk_signoff: ['risk_signoff'],
  write_workbook: ['write_workbook'],
  final_signoff: ['final_signoff', 'completed'],
};

function visibleStage(stage: string) {
  if (stage === 'financial_signoff') return 'financial_evidence';
  if (stage === 'completed' || stage === 'external_score') {
    return 'final_signoff';
  }
  if (stage === 'intake_excel') return 'resolve_entity';
  return stage;
}

const qualitativeOptions: Record<string, string[]> = {
  pay_habits: [
    'Prompt',
    '1 to 15 days arrear',
    '15 to 20 arrear',
    'Over 20 days arrear',
    'No history',
  ],
  company_type: [
    'Public listed',
    'Private - audited',
    'Private - unaudited',
    'Bankruptcy/Chapter 11',
  ],
  sovereign_rating: ['grade A or above', 'grade B', 'grade C or Not rated'],
  customer_distinction: [
    'Priority - Above Average Margin',
    'Strategic',
    'Standard',
    'Not classified',
  ],
};

const qualitativeLabels: Record<string, string> = {
  pay_habits: '近 12 个月付款表现',
  company_type: '企业类型',
  sovereign_rating: '国家风险档位',
  customer_distinction: '客户重要性',
};

const financialLabels: Record<string, string> = {
  net_sales: '营业收入',
  cost_of_goods_sold: '营业成本',
  operating_income: '营业利润',
  interest_expense: '利息费用',
  cash: '现金及现金等价物',
  accounts_receivable: '应收账款',
  inventories: '存货',
  other_current_assets: '其他流动资产',
  total_current_assets: '流动资产合计',
  property_plant_equipment: '固定资产',
  intangible_assets: '无形资产',
  goodwill: '商誉',
  other_non_current_assets: '其他非流动资产',
  total_assets: '资产总额',
  accounts_payable: '应付账款',
  short_term_debt: '短期借款',
  other_current_liabilities: '其他流动负债',
  total_current_liabilities: '流动负债合计',
  long_term_debt: '长期借款',
  other_non_current_liabilities: '其他非流动负债',
  total_liabilities: '负债总额',
  total_equity: '所有者权益',
  sales: '营业收入',
  ebit: '息税前利润',
  net_income: '净利润',
  depreciation_amortization: '折旧与摊销',
  operating_cash_flow: '经营活动现金流净额',
  capex: '资本性支出',
};

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  sources?: string[];
};

type Candidate = {
  entity_id: string;
  name: string;
  score: number;
  address?: string;
  city?: string;
  state?: string;
  country?: string;
  postcode?: string;
  rating?: string;
  paydex?: number;
  reasons?: string[];
};

type FinancialValue = {
  field: string;
  period_end?: string;
  value: number | string;
  unit_multiplier?: number;
  currency?: string;
  page?: number;
  source_text?: string;
  confidence?: string;
};

type RiskSource = {
  id: string;
  citation: string;
  text: string;
  source_type?: string;
  access_scope?: string;
};

type RiskDraft = {
  company_description?: string;
  risk_comments?: string;
  stance?: string;
  qualitative?: Record<string, { choice: string; reason: string }>;
  updated_at?: string;
  draft_mode?: string;
  edited_by?: string;
  generation_warning?: string;
};

function DownloadButton({
  path,
  filename,
  label,
  recovery,
  className,
}: {
  path: string;
  filename: string;
  label: string;
  recovery: string;
  className?: string;
}) {
  const [downloading, setDownloading] = useState(false);
  const handleDownload = async () => {
    setDownloading(true);
    try {
      const downloadedFilename = await craApi.download(path, filename);
      toast.success('文件已开始下载', { description: downloadedFilename });
    } catch (downloadError) {
      toast.error(
        downloadError instanceof Error ? downloadError.message : '文件下载失败',
        { description: recovery }
      );
    } finally {
      setDownloading(false);
    }
  };
  return (
    <Button
      type="button"
      variant="outline"
      onClick={() => void handleDownload()}
      disabled={downloading}
      aria-busy={downloading}
      className={className}
    >
      {downloading ? (
        <IconLoader2 className="animate-spin" />
      ) : (
        <IconDownload />
      )}{' '}
      {downloading ? '正在准备文件…' : label}
    </Button>
  );
}

function PanelHeader({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <header className="mb-5">
      <p className="text-[11px] font-semibold tracking-[0.12em] text-blue-700 uppercase">
        {eyebrow}
      </p>
      <h2 className="mt-1.5 text-lg font-semibold tracking-[-0.02em] text-slate-950">
        {title}
      </h2>
      <p className="mt-1.5 text-sm leading-6 text-slate-600">{description}</p>
    </header>
  );
}

function EntityReview({
  item,
  task,
  acting,
  selectedId,
  onSelect,
  onConfirm,
}: {
  item: CraCase;
  task: HumanTask;
  acting: boolean;
  selectedId: string;
  onSelect: (id: string) => void;
  onConfirm: () => void;
}) {
  const candidates = (task.payload.candidates ||
    item.notes.entity_candidates ||
    []) as Candidate[];
  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 md:p-6">
      <PanelHeader
        eyebrow="需要你的判断"
        title="确认企业主体"
        description="Agent 只做候选排序，不会自动合并。请同时核对 Excel 原始客户、SAP BP 与候选企业资料。"
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['Excel 客户名称', item.customer.name || '—'],
          ['SAP BP', item.bp || '—'],
          [
            '登记地区',
            [item.customer.city, item.customer.state, item.customer.country]
              .filter(Boolean)
              .join(' · ') || '—',
          ],
          ['申请额度', money(item.requested_cl_usd)],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200"
          >
            <p className="text-[11px] text-slate-500">{label}</p>
            <p
              className="mt-1 truncate text-sm font-semibold text-slate-900"
              title={String(value)}
            >
              {value}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-5 overflow-x-auto rounded-xl ring-1 ring-slate-200">
        <table className="w-full min-w-[760px] border-collapse text-left text-xs">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th className="w-12 px-4 py-3 font-medium">选择</th>
              <th className="px-4 py-3 font-medium">候选主体</th>
              <th className="px-4 py-3 font-medium">注册地址</th>
              <th className="px-4 py-3 font-medium">评级 / PAYDEX</th>
              <th className="px-4 py-3 text-right font-medium">匹配度</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {candidates.map((candidate) => (
              <tr
                key={candidate.entity_id}
                onClick={() => onSelect(candidate.entity_id)}
                className={`cursor-pointer transition-colors ${selectedId === candidate.entity_id ? 'bg-blue-50/80' : 'bg-white hover:bg-slate-50'}`}
              >
                <td className="px-4 py-4">
                  <input
                    type="radio"
                    name="entity"
                    checked={selectedId === candidate.entity_id}
                    onChange={() => onSelect(candidate.entity_id)}
                    aria-label={`选择 ${candidate.name}`}
                    className="accent-blue-700"
                  />
                </td>
                <td className="px-4 py-4">
                  <p className="font-semibold text-slate-950">
                    {candidate.name}
                  </p>
                  <p className="mt-1 text-[11px] text-slate-500">
                    {candidate.entity_id}
                  </p>
                  {!!candidate.reasons?.length && (
                    <p className="mt-1 text-[11px] text-blue-700">
                      {candidate.reasons.join(' · ')}
                    </p>
                  )}
                </td>
                <td className="px-4 py-4 leading-5 text-slate-600">
                  {candidate.address || '—'}
                  <br />
                  {[
                    candidate.city,
                    candidate.state,
                    candidate.postcode,
                    candidate.country,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </td>
                <td className="px-4 py-4 text-slate-700">
                  {candidate.rating || '—'} / {candidate.paydex ?? '—'}
                </td>
                <td className="px-4 py-4 text-right font-semibold text-blue-700 tabular-nums">
                  {candidate.score}%
                </td>
              </tr>
            ))}
            {!candidates.length && (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-10 text-center text-slate-500"
                >
                  尚未找到候选主体，请让 Agent 重新检索。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <DownloadButton
          path={`/cases/${item.case_id}/source`}
          filename={`${item.case_id}_source.xlsx`}
          label="下载原始 Excel"
          recovery="请确认案件已成功导入原始 Excel，或刷新案件后重试。"
          className="rounded-xl bg-white"
        />
        <Button
          onClick={onConfirm}
          disabled={acting || !selectedId}
          className="rounded-xl bg-blue-700 hover:bg-blue-800"
        >
          {acting ? <IconLoader2 className="animate-spin" /> : <IconCheck />}{' '}
          确认所选主体
        </Button>
      </div>
    </section>
  );
}

function FinancialReview({
  item,
  acting,
  onConfirm,
  readOnly = false,
}: {
  item: CraCase;
  acting: boolean;
  onConfirm: () => void;
  readOnly?: boolean;
}) {
  const values = (item.notes.financial_values || []) as FinancialValue[];
  const source = item.notes.financial_source as
    | {
        type?: string;
        warning?: string;
        label?: string;
        filename?: string;
        stored_filename?: string;
        pages_used?: unknown[];
      }
    | undefined;
  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 md:p-6">
      <PanelHeader
        eyebrow="需要你的判断"
        title="签署财务证据"
        description="核对 Agent 抽取出的数值、单位、页码和原文。表格是本步骤的交付结果，不确认就不会进入风险分析。"
      />
      {source?.type === 'demo' && (
        <div className="mb-4 flex gap-3 rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-900 ring-1 ring-amber-200">
          <IconFlask size={17} className="mt-0.5 shrink-0" />
          <p>
            <span className="font-semibold">演示数据：</span>
            {source.warning || '该数据不是客户正式财报，仅用于验证产品流程。'}
            演示值由历史 SAP
            数据生成，没有对应的财报原页，因此不会标为高置信度。
          </p>
        </div>
      )}
      {source?.type === 'report' && (
        <div className="mb-4 flex flex-col gap-2 rounded-xl bg-blue-50 p-3 text-xs leading-5 text-blue-950 ring-1 ring-blue-200 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <IconFileText size={17} className="mt-0.5 shrink-0 text-blue-700" />
            <p className="min-w-0">
              <span className="font-semibold">正式财报：</span>
              <span className="break-all">
                {source.filename || source.label}
              </span>
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <span className="text-blue-700">
              已定位{' '}
              {Array.isArray(source.pages_used)
                ? source.pages_used.length
                : '—'}{' '}
              个报表页
            </span>
            {source.stored_filename && (
              <Button
                asChild
                size="sm"
                variant="outline"
                className="h-8 bg-white"
              >
                <a
                  href={financialReportSourceUrl(item.case_id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <IconEye size={15} /> 查看财报原文
                </a>
              </Button>
            )}
          </div>
        </div>
      )}
      <div className="max-h-[480px] overflow-auto rounded-xl ring-1 ring-slate-200">
        <table className="w-full min-w-[860px] border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-slate-500 shadow-[0_1px_0_#e2e8f0]">
            <tr>
              <th className="px-4 py-3 font-medium">字段</th>
              <th className="px-4 py-3 text-right font-medium">抽取值</th>
              <th className="px-4 py-3 font-medium">单位 / 币种</th>
              <th className="px-4 py-3 font-medium">页码</th>
              <th className="px-4 py-3 font-medium">置信度</th>
              <th className="px-4 py-3 font-medium">原文证据</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 bg-white">
            {values.map((value) => (
              <tr key={value.field}>
                <td className="px-4 py-3">
                  <p className="font-semibold text-slate-900">
                    {financialLabels[value.field] || value.field}
                  </p>
                  <p className="mt-0.5 text-[10px] text-slate-400">
                    {value.field}
                  </p>
                </td>
                <td className="px-4 py-3 text-right font-semibold text-slate-950 tabular-nums">
                  {new Intl.NumberFormat('zh-CN').format(
                    Number(value.value) || 0
                  )}
                </td>
                <td className="px-4 py-3 text-slate-600">
                  ×{' '}
                  {new Intl.NumberFormat('zh-CN').format(
                    value.unit_multiplier || 1
                  )}{' '}
                  · {value.currency || item.statement_currency}
                </td>
                <td className="px-4 py-3 text-slate-600 tabular-nums">
                  {source?.type === 'demo' ? (
                    <span className="text-slate-400">无原页</span>
                  ) : value.page && source?.type === 'report' ? (
                    <a
                      href={financialReportSourceUrl(item.case_id, value.page)}
                      target="_blank"
                      rel="noreferrer"
                      className="font-semibold text-blue-700 hover:underline"
                    >
                      查看 P.{value.page}
                    </a>
                  ) : value.page ? (
                    `P.${value.page}`
                  ) : (
                    '—'
                  )}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`rounded-md px-2 py-1 text-[10px] font-semibold ${source?.type === 'demo' ? 'bg-slate-100 text-slate-600' : value.confidence === 'high' ? 'bg-emerald-50 text-emerald-700' : value.confidence === 'low' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-800'}`}
                  >
                    {source?.type === 'demo'
                      ? '演示'
                      : value.confidence === 'high'
                        ? '高'
                        : value.confidence === 'low'
                          ? '低'
                          : value.confidence === 'medium'
                            ? '中'
                            : '未标注'}
                  </span>
                </td>
                <td className="max-w-[320px] px-4 py-3 leading-5 text-slate-600">
                  {value.source_text || '—'}
                </td>
              </tr>
            ))}
            {!values.length && (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-10 text-center text-slate-500"
                >
                  尚无抽取结果，不能签署。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <DownloadButton
          path={`/cases/${item.case_id}/financial-evidence.csv`}
          filename={`${item.case_id}_financial_evidence.csv`}
          label="下载抽取结果 CSV"
          recovery="请先完成财务抽取与校验，再下载证据 CSV。"
          className="rounded-xl bg-white"
        />
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500">
            {values.length} / 21 个字段
          </span>
          {!readOnly && (
            <Button
              onClick={onConfirm}
              disabled={acting || !values.length}
              className="rounded-xl bg-blue-700 hover:bg-blue-800"
            >
              {acting ? (
                <IconLoader2 className="animate-spin" />
              ) : (
                <IconCheck />
              )}{' '}
              确认财务证据
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

function RiskReview({
  item,
  acting,
  editable,
  signable,
  onSave,
  onRegenerate,
  onConfirm,
}: {
  item: CraCase;
  acting: boolean;
  editable: boolean;
  signable: boolean;
  onSave: (opinion: Record<string, unknown>) => void;
  onRegenerate: () => void;
  onConfirm: (opinion: Record<string, unknown>) => void;
}) {
  const draft = (item.notes.risk_draft || {}) as RiskDraft;
  const sources = (item.notes.risk_sources || []) as RiskSource[];
  const [companyDescription, setCompanyDescription] = useState(
    String(draft.company_description || '')
  );
  const [riskComments, setRiskComments] = useState(
    String(draft.risk_comments || '')
  );
  const [qualitative, setQualitative] = useState<
    Record<string, { choice: string; reason: string }>
  >(draft.qualitative || {});

  /* The server revision is authoritative after save and regeneration. */
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setCompanyDescription(String(draft.company_description || ''));
    setRiskComments(String(draft.risk_comments || ''));
    setQualitative(draft.qualitative || {});
  }, [
    draft.company_description,
    draft.qualitative,
    draft.risk_comments,
    draft.updated_at,
    item.case_id,
  ]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const opinion = {
    company_description: companyDescription,
    risk_comments: riskComments,
    stance: draft.stance || '',
    qualitative,
  };
  const dirty =
    companyDescription !== String(draft.company_description || '') ||
    riskComments !== String(draft.risk_comments || '') ||
    JSON.stringify(qualitative) !== JSON.stringify(draft.qualitative || {});
  const complete =
    companyDescription.trim() &&
    riskComments.trim() &&
    Object.keys(qualitativeOptions).every((key) => qualitative[key]?.choice);
  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 md:p-6">
      <PanelHeader
        eyebrow={signable ? '需要你的判断' : '案件核心产物'}
        title="风险评测与意见"
        description="Agent 根据已签署的财务证据和当前可见 RAG 材料起草。分析师可修改并保存；最终综合分和 Risk Category 仍由 Excel 宏 / SAP 计算。"
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <article className="space-y-5 rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200 md:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-4">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
              <IconFileText size={17} className="text-blue-700" /> Agent
              风险草稿
            </div>
            <div className="flex items-center gap-2 text-[10px] text-slate-500">
              <span className="rounded-md bg-white px-2 py-1 ring-1 ring-slate-200">
                {draft.draft_mode === 'llm'
                  ? '模型起草'
                  : draft.draft_mode === 'analyst_edited'
                    ? '分析师已修改'
                    : '规则型草稿'}
              </span>
              <span>
                {draft.edited_by || 'Agent'} · {dateTime(draft.updated_at)}
              </span>
            </div>
          </div>
          {draft.generation_warning && (
            <div className="rounded-lg bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-900 ring-1 ring-amber-200">
              {draft.generation_warning}
            </div>
          )}
          <div>
            <label
              htmlFor="company-description"
              className="text-xs font-semibold text-slate-950"
            >
              企业介绍
            </label>
            <Textarea
              id="company-description"
              value={companyDescription}
              onChange={(event) => setCompanyDescription(event.target.value)}
              disabled={!editable || acting}
              className="mt-2 min-h-24 resize-y bg-white text-sm leading-6 text-slate-800"
            />
          </div>
          <div>
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div>
                <h3 className="text-xs font-semibold text-slate-950">
                  定性评分建议
                </h3>
                <p className="mt-1 text-[11px] text-slate-500">
                  选项会回填 Excel；综合分由原宏计算。
                </p>
              </div>
              <span className="text-[10px] text-slate-500">4 项均需复核</span>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {Object.entries(qualitativeOptions).map(([key, options]) => (
                <div
                  key={key}
                  className="rounded-lg bg-white p-3 ring-1 ring-slate-200"
                >
                  <label
                    htmlFor={`qual-${key}`}
                    className="text-[11px] font-semibold text-slate-700"
                  >
                    {qualitativeLabels[key]}
                  </label>
                  <select
                    id={`qual-${key}`}
                    value={qualitative[key]?.choice || ''}
                    onChange={(event) =>
                      setQualitative((current) => ({
                        ...current,
                        [key]: {
                          choice: event.target.value,
                          reason: current[key]?.reason || '',
                        },
                      }))
                    }
                    disabled={!editable || acting}
                    className="mt-2 h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 text-xs text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:bg-slate-100"
                  >
                    <option value="">请选择</option>
                    {options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                  <p className="mt-2 min-h-8 text-[10px] leading-4 text-slate-500">
                    {qualitative[key]?.reason || '尚无起草理由'}
                  </p>
                </div>
              ))}
            </div>
          </div>
          <div>
            <label
              htmlFor="risk-comments"
              className="text-xs font-semibold text-slate-950"
            >
              风险意见
            </label>
            <Textarea
              id="risk-comments"
              value={riskComments}
              onChange={(event) => setRiskComments(event.target.value)}
              disabled={!editable || acting}
              className="mt-2 min-h-64 resize-y bg-white text-sm leading-6 text-slate-800"
            />
            <p className="mt-2 text-[10px] text-slate-500">
              必须覆盖偿债能力、杠杆、盈利能力、付款记录和国家风险；保留 [S#]
              引用。
            </p>
          </div>
        </article>
        <aside className="rounded-xl bg-white p-4 ring-1 ring-slate-200">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <IconDatabase size={17} className="text-blue-700" />
              <h3 className="text-xs font-semibold text-slate-950">
                RAG 引用库
              </h3>
            </div>
            <span className="text-[10px] text-slate-500">
              {sources.length} 条
            </span>
          </div>
          <div className="max-h-[390px] space-y-2 overflow-y-auto pr-1">
            {sources.map((source) => (
              <details
                key={source.id}
                className="group rounded-lg bg-slate-50 ring-1 ring-slate-200"
              >
                <summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-3">
                  <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-bold text-blue-700">
                    {source.id}
                  </span>
                  <span className="min-w-0 flex-1 text-[11px] leading-4 font-medium text-slate-800">
                    {source.citation}
                  </span>
                  <IconChevronDown
                    size={14}
                    className="mt-0.5 shrink-0 text-slate-400 transition-transform group-open:rotate-180"
                  />
                </summary>
                <div className="border-t border-slate-200 px-3 py-3">
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    <span className="rounded bg-white px-2 py-1 text-[10px] text-slate-600 ring-1 ring-slate-200">
                      {source.source_type || 'policy'}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded bg-white px-2 py-1 text-[10px] text-slate-600 ring-1 ring-slate-200">
                      <IconLock size={11} />
                      {source.access_scope || '信用分析师'}
                    </span>
                  </div>
                  <p className="text-[11px] leading-5 text-slate-600">
                    {source.text}
                  </p>
                </div>
              </details>
            ))}
            {!sources.length && (
              <p className="rounded-lg bg-slate-50 p-4 text-center text-xs text-slate-500">
                尚无检索来源。
              </p>
            )}
          </div>
        </aside>
      </div>
      <div className="mt-5 flex flex-col gap-3 border-t border-slate-200 pt-5 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-[11px] text-slate-500">
          {dirty
            ? '有尚未保存的修改'
            : `已保存 ${((item.notes.risk_opinion_versions || []) as unknown[]).length || 1} 个版本`}
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          {editable && (
            <Button
              variant="outline"
              onClick={onRegenerate}
              disabled={acting}
              className="rounded-xl bg-white"
            >
              <IconSparkles /> 重新起草
            </Button>
          )}
          {editable && (
            <Button
              variant="outline"
              onClick={() => onSave(opinion)}
              disabled={acting || !dirty || !complete}
              className="rounded-xl bg-white"
            >
              <IconDeviceFloppy /> 保存修改
            </Button>
          )}
          {signable && (
            <Button
              onClick={() => onConfirm(opinion)}
              disabled={acting || !complete || !sources.length}
              className="rounded-xl bg-blue-700 hover:bg-blue-800"
            >
              {acting ? (
                <IconLoader2 className="animate-spin" />
              ) : (
                <IconCheck />
              )}{' '}
              确认并签署
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

function WorkbookReview({
  item,
  task,
  acting,
  onRun,
  onConfirm,
}: {
  item: CraCase;
  task?: HumanTask;
  acting: boolean;
  onRun: () => void;
  onConfirm: () => void;
}) {
  const generated = Boolean(item.notes.export_path);
  if (!generated) {
    return (
      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 md:p-6">
        <PanelHeader
          eyebrow="Agent 下一步"
          title="生成 CRA Excel 新版本"
          description="系统会复制本案原始交接包，写入已确认的数据并保存为新的案件版本；原文件不会被覆盖。"
        />
        <div className="rounded-xl bg-blue-50 p-4 text-sm leading-6 text-blue-950 ring-1 ring-blue-200">
          输出位置：案件档案 →
          交付文件。生成完成后，本区域会显示文件名、生成时间和下载按钮。
        </div>
        <div className="mt-5 flex justify-end">
          <Button
            onClick={onRun}
            disabled={acting}
            className="rounded-xl bg-blue-700 hover:bg-blue-800"
          >
            {acting ? (
              <IconLoader2 className="animate-spin" />
            ) : (
              <IconFileSpreadsheet />
            )}{' '}
            生成 Excel 新版本
          </Button>
        </div>
      </section>
    );
  }
  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 md:p-6">
      <PanelHeader
        eyebrow={task ? '需要你的判断' : '生成结果'}
        title="CRA Excel 已生成"
        description="文件已写入本案档案。请下载复核；通过后再完成最终签署。"
      />
      <div className="flex flex-col gap-4 rounded-xl bg-emerald-50 p-4 ring-1 ring-emerald-200 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-white text-emerald-700 ring-1 ring-emerald-200">
            <IconFileSpreadsheet size={20} />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-emerald-950">
              {String(
                item.notes.export_file_name || `${item.case_id}_CRA_review.xlsx`
              )}
            </p>
            <p className="mt-1 text-xs text-emerald-800">
              案件档案 / 交付文件 ·{' '}
              {dateTime(String(item.notes.generated_at || ''))}
            </p>
          </div>
        </div>
        <DownloadButton
          path={`/cases/${item.case_id}/export`}
          filename={String(
            item.notes.export_file_name || `${item.case_id}_CRA_review.xlsx`
          )}
          label="下载并复核"
          recovery="请重新生成 CRA Excel，确认生成成功后再下载。"
          className="shrink-0 rounded-xl bg-white text-emerald-800"
        />
      </div>
      {task && (
        <div className="mt-5 flex justify-end">
          <Button
            onClick={onConfirm}
            disabled={acting}
            className="rounded-xl bg-blue-700 hover:bg-blue-800"
          >
            {acting ? <IconLoader2 className="animate-spin" /> : <IconCheck />}{' '}
            完成最终复核
          </Button>
        </div>
      )}
    </section>
  );
}

function RunningStep({
  item,
  acting,
  onRun,
}: {
  item: CraCase;
  acting: boolean;
  onRun: () => void;
}) {
  const descriptions: Record<string, string> = {
    resolve_entity:
      'Agent 将根据名称、地址、国家与企业库做候选排序，然后交给你确认。',
    financial_evidence:
      'Agent 将读取当期财报，抽取 21 个财务字段，并保留页码与原文。',
    retrieve_context:
      'Agent 将检索内部规则、历史案件与公开资料，生成带引用的风险意见草稿。',
  };
  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 md:p-6">
      <PanelHeader
        eyebrow="当前可执行"
        title={item.workflow.current_task}
        description={
          descriptions[item.workflow.current_stage] ||
          'Agent 将执行当前步骤，并在需要判断时暂停。'
        }
      />
      <div className="flex justify-end">
        <Button
          onClick={onRun}
          disabled={acting}
          className="rounded-xl bg-blue-700 hover:bg-blue-800"
        >
          {acting ? (
            <IconLoader2 className="animate-spin" />
          ) : (
            <IconPlayerPlay />
          )}{' '}
          让 Agent 执行当前步骤
        </Button>
      </div>
    </section>
  );
}

function BlockedFinancials({
  item,
  acting,
  onUpload,
  onRun,
  onDemo,
}: {
  item: CraCase;
  acting: boolean;
  onUpload: (file: File) => Promise<boolean>;
  onRun: () => void;
  onDemo: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState('');
  const [dragging, setDragging] = useState(false);
  const activeBadCase = item.bad_cases?.find(
    (badCase) => badCase.status === 'open'
  );
  const chooseFile = (next: File | null) => {
    if (!next) return;
    if (!next.name.toLowerCase().endsWith('.pdf')) {
      setFile(null);
      setFileError('文件格式不正确，请上传 PDF 财务报告。');
      return;
    }
    if (next.size > 20 * 1024 * 1024) {
      setFile(null);
      setFileError('文件超过 20 MB 上限，请压缩后重试。');
      return;
    }
    setFile(next);
    setFileError('');
  };
  const upload = async () => {
    if (!file) return;
    if (await onUpload(file)) setFile(null);
  };
  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 md:p-6">
      <PanelHeader
        eyebrow="第 02 步 · 财务证据"
        title="上传当期财务报告"
        description={
          item.workflow.blocker ||
          '上传正式财报后，Agent 会定位报表页、抽取字段并交给你逐项签署。'
        }
      />
      {activeBadCase && (
        <div className="mb-5 grid gap-3 rounded-xl bg-red-50 p-4 text-xs text-red-900 ring-1 ring-red-200 sm:grid-cols-3">
          <div>
            <p className="text-red-500">分级</p>
            <p className="mt-1 font-semibold">
              {activeBadCase.priority} · {activeBadCase.exposure}/
              {activeBadCase.impact}
            </p>
          </div>
          <div>
            <p className="text-red-500">发现方式</p>
            <p className="mt-1 font-semibold">
              {activeBadCase.detection_source}
            </p>
          </div>
          <div>
            <p className="text-red-500">处置</p>
            <p className="mt-1 font-semibold">{activeBadCase.response}</p>
          </div>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
        <div className="min-w-0">
          {!file ? (
            <label
              onDragEnter={() => setDragging(true)}
              onDragLeave={() => setDragging(false)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event: DragEvent<HTMLLabelElement>) => {
                event.preventDefault();
                setDragging(false);
                chooseFile(event.dataTransfer.files?.[0] || null);
              }}
              className={`group grid min-h-52 cursor-pointer place-items-center rounded-2xl border border-dashed p-6 text-center transition focus-within:ring-2 focus-within:ring-blue-600 ${
                dragging
                  ? 'border-blue-500 bg-blue-50'
                  : 'border-slate-300 bg-slate-50 hover:border-blue-400 hover:bg-blue-50/50'
              }`}
            >
              <input
                type="file"
                accept="application/pdf,.pdf"
                className="sr-only"
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  chooseFile(event.target.files?.[0] || null)
                }
              />
              <div>
                <span className="mx-auto grid size-12 place-items-center rounded-xl bg-white text-blue-700 shadow-sm ring-1 ring-slate-200 transition group-hover:-translate-y-0.5">
                  <IconUpload size={22} />
                </span>
                <p className="mt-4 text-sm font-semibold text-slate-950">
                  拖入财报，或点击选择文件
                </p>
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  PDF · 最大 20 MB · 建议使用带文字层的审计报告
                </p>
              </div>
            </label>
          ) : (
            <div className="flex min-h-52 flex-col justify-between rounded-2xl bg-slate-50 p-5 ring-1 ring-slate-200">
              <div className="flex min-w-0 items-start gap-3">
                <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-red-50 text-red-700 ring-1 ring-red-100">
                  <IconFileText size={22} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-slate-950">
                    {file.name}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {(file.size / 1024 / 1024).toFixed(2)} MB · 待上传
                  </p>
                </div>
                <button
                  type="button"
                  aria-label="移除文件"
                  disabled={acting}
                  onClick={() => {
                    setFile(null);
                    setFileError('');
                  }}
                  className="grid size-9 shrink-0 place-items-center rounded-lg text-slate-500 hover:bg-white hover:text-slate-950 disabled:opacity-50"
                >
                  <IconX size={18} />
                </button>
              </div>
              <div>
                <p className="mb-3 text-xs leading-5 text-slate-500">
                  上传后将自动抽取案件期间{' '}
                  {String(item.new_year.period_end || '')} 的财务字段。
                </p>
                <Button
                  type="button"
                  onClick={() => void upload()}
                  disabled={acting}
                  className="h-11 w-full rounded-xl bg-blue-700 hover:bg-blue-800"
                >
                  {acting ? (
                    <IconLoader2 className="animate-spin" />
                  ) : (
                    <IconUpload />
                  )}
                  {acting ? '正在解析财报…' : '上传并开始抽取'}
                </Button>
              </div>
            </div>
          )}
          {fileError && (
            <p role="alert" className="mt-3 text-xs font-medium text-red-600">
              {fileError}
            </p>
          )}
        </div>
        <aside className="rounded-2xl bg-slate-50 p-5 ring-1 ring-slate-200">
          <p className="text-xs font-semibold text-slate-900">上传后自动完成</p>
          <ol className="mt-4 space-y-3 text-xs leading-5 text-slate-600">
            <li className="flex gap-2">
              <span className="font-semibold text-blue-700">1.</span>{' '}
              定位三大财务报表页
            </li>
            <li className="flex gap-2">
              <span className="font-semibold text-blue-700">2.</span>{' '}
              抽取数值、单位与原文
            </li>
            <li className="flex gap-2">
              <span className="font-semibold text-blue-700">3.</span>{' '}
              校验后进入人工签署
            </li>
          </ol>
        </aside>
      </div>
      <div className="mt-5 flex flex-col gap-3 rounded-xl bg-blue-950 p-4 text-white sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          {item.workflow.status === 'blocked' ? (
            <IconFlask size={20} className="mt-0.5 shrink-0 text-blue-300" />
          ) : (
            <IconFileText size={20} className="mt-0.5 shrink-0 text-blue-300" />
          )}
          <p className="text-xs leading-5 text-blue-100">
            {item.workflow.status === 'blocked'
              ? '如果暂时没有正式财报，可使用上期 SAP 数据生成明确标注的演示抽取。'
              : '案件已经登记了财报材料时，可直接让 Agent 读取；上传新文件会替换本次抽取来源。'}
          </p>
        </div>
        <Button
          onClick={item.workflow.status === 'blocked' ? onDemo : onRun}
          disabled={acting}
          className="shrink-0 rounded-xl bg-white text-blue-950 hover:bg-blue-50"
        >
          {acting ? (
            <IconLoader2 className="animate-spin" />
          ) : (
            <IconPlayerPlay />
          )}{' '}
          {item.workflow.status === 'blocked'
            ? '使用演示数据继续'
            : '读取已有材料'}
        </Button>
      </div>
    </section>
  );
}

function AgentTimeline({ item }: { item: CraCase }) {
  return (
    <div className="space-y-0">
      {item.events.map((event, index) => (
        <div
          key={event.id}
          className="relative grid grid-cols-[28px_1fr] gap-3 pb-5 last:pb-0"
        >
          {index !== item.events.length - 1 && (
            <span className="absolute top-6 bottom-0 left-[13px] w-px bg-slate-200" />
          )}
          <span
            className={`relative z-10 mt-0.5 grid size-7 place-items-center rounded-full ${event.event_type === 'blocked' ? 'bg-red-50 text-red-700' : event.event_type === 'human_gate' ? 'bg-amber-50 text-amber-800' : 'bg-blue-50 text-blue-700'}`}
          >
            {event.event_type === 'human_gate' ? (
              <IconUserCheck size={14} />
            ) : event.event_type === 'blocked' ? (
              <IconAlertTriangle size={14} />
            ) : (
              <IconCheck size={14} />
            )}
          </span>
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-xs font-semibold text-slate-900">
                {event.title}
              </h4>
              <time className="text-[10px] text-slate-400 tabular-nums">
                {dateTime(event.created_at)}
              </time>
            </div>
            <p className="mt-1 text-xs leading-5 text-slate-600">
              {event.detail}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

function EvidenceSummary({ item }: { item: CraCase }) {
  const candidates = (item.notes.entity_candidates || []) as Candidate[];
  const sources = (item.notes.risk_sources || []) as RiskSource[];
  const values = (item.notes.financial_values || []) as FinancialValue[];
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {[
        ['主体材料', `${candidates.length} 个候选`, IconUserCheck],
        ['财务证据', `${values.length} / 21 字段`, IconShieldCheck],
        ['RAG 引用', `${sources.length} 条来源`, IconDatabase],
      ].map(([title, value, Icon]) => (
        <div
          key={String(title)}
          className="flex items-center gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200"
        >
          <span className="grid size-8 place-items-center rounded-lg bg-white text-blue-700 ring-1 ring-slate-200">
            <Icon size={16} />
          </span>
          <div>
            <p className="text-[11px] text-slate-500">{String(title)}</p>
            <p className="mt-0.5 text-xs font-semibold text-slate-900">
              {String(value)}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

function CompletedStageView({
  item,
  stage,
  snapshot,
}: {
  item: CraCase;
  stage: string;
  snapshot?: StageSnapshot;
}) {
  const reconstructed =
    snapshot?.payload.history_status === 'reconstructed' ||
    snapshot?.title === `${snapshot?.stage} 已完成`;
  if (!snapshot || !snapshot.payload.notes || reconstructed) {
    return (
      <section className="border-border bg-card rounded-xl border p-6 md:p-8">
        <div className="mx-auto max-w-xl text-center">
          <span className="mx-auto grid size-11 place-items-center rounded-full bg-amber-50 text-amber-700 ring-1 ring-amber-200">
            <IconHistory size={21} />
          </span>
          <h2 className="text-foreground mt-4 text-lg font-semibold tracking-[-0.02em]">
            该步骤没有可验证的历史快照
          </h2>
          <p className="text-muted-foreground mt-2 text-sm leading-6">
            这是启用步骤快照之前完成的旧案件。系统不会用当前数据冒充当时证据；从下一次流程流转开始，会在后台自动保存可回溯结果。
          </p>
        </div>
      </section>
    );
  }

  const notes = snapshot.payload.notes;
  const historicalItem = { ...item, notes };
  const selectedEntity = notes.selected_entity as Candidate | undefined;
  const candidates = (notes.entity_candidates || []) as Candidate[];
  const sources = (notes.risk_sources || []) as RiskSource[];
  const completedAt = snapshot?.completed_at;

  if (stage === 'financial_evidence') {
    return (
      <FinancialReview
        item={historicalItem}
        acting={false}
        readOnly
        onConfirm={() => undefined}
      />
    );
  }

  if (stage === 'risk_signoff') {
    return (
      <RiskReview
        item={historicalItem}
        acting={false}
        editable={false}
        signable={false}
        onSave={() => undefined}
        onRegenerate={() => undefined}
        onConfirm={() => undefined}
      />
    );
  }

  if (stage === 'write_workbook') {
    return (
      <WorkbookReview
        item={historicalItem}
        acting={false}
        onRun={() => undefined}
        onConfirm={() => undefined}
      />
    );
  }

  if (stage === 'resolve_entity') {
    return (
      <section className="border-border bg-card rounded-xl border p-5 md:p-6">
        <h2 className="text-foreground text-lg font-semibold tracking-[-0.02em]">
          企业主体核验结果
        </h2>
        <p className="text-muted-foreground mt-2 text-sm leading-6">
          已核对 Excel 客户、SAP BP
          与企业库候选。这里展示当时确认的主体和候选依据。
        </p>
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          <div className="bg-muted rounded-lg p-4">
            <p className="text-muted-foreground text-xs">已确认主体</p>
            <p className="text-foreground mt-1 text-sm font-semibold">
              {selectedEntity?.name || '未记录确认主体'}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              {selectedEntity?.entity_id || '—'}
            </p>
          </div>
          <div className="bg-muted rounded-lg p-4">
            <p className="text-muted-foreground text-xs">核验范围</p>
            <p className="text-foreground mt-1 text-sm font-semibold">
              {candidates.length} 个候选 · BP {item.bp}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              {selectedEntity?.address || item.customer.name || '—'}
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (stage === 'retrieve_context') {
    return (
      <section className="border-border bg-card rounded-xl border p-5 md:p-6">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-foreground text-lg font-semibold tracking-[-0.02em]">
              规则与历史检索结果
            </h2>
            <p className="text-muted-foreground mt-2 text-sm leading-6">
              风险意见使用的制度、公开资料和历史经验均保留引用，不重新执行检索。
            </p>
          </div>
          <span className="text-muted-foreground text-xs tabular-nums">
            {sources.length} 条来源
          </span>
        </div>
        <div className="divide-border border-border mt-5 divide-y border-y">
          {sources.map((source) => (
            <div key={source.id} className="py-4">
              <div className="flex items-start gap-3">
                <span className="bg-primary/10 text-primary rounded-md px-2 py-1 text-xs font-semibold">
                  {source.id}
                </span>
                <div className="min-w-0">
                  <p className="text-foreground text-sm font-semibold break-words">
                    {source.citation}
                  </p>
                  <p className="text-muted-foreground mt-1 line-clamp-3 text-xs leading-5">
                    {source.text}
                  </p>
                </div>
              </div>
            </div>
          ))}
          {!sources.length && (
            <p className="text-muted-foreground py-8 text-center text-sm">
              此阶段没有保存检索来源。
            </p>
          )}
        </div>
      </section>
    );
  }

  return (
    <section className="border-border bg-card rounded-xl border p-5 md:p-6">
      <div className="flex items-start gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-emerald-50 text-emerald-700">
          <IconCheck size={20} />
        </span>
        <div>
          <h2 className="text-foreground text-lg font-semibold tracking-[-0.02em]">
            最终复核已完成
          </h2>
          <p className="text-muted-foreground mt-2 text-sm leading-6">
            人工签署、证据版本和交付文件已归入案件审计记录。
            {completedAt ? ` 完成于 ${dateTime(completedAt)}。` : ''}
          </p>
        </div>
      </div>
      <div className="mt-5">
        <EvidenceSummary item={historicalItem} />
      </div>
    </section>
  );
}

function AuditCaseFile({ item }: { item: CraCase }) {
  return (
    <details className="group rounded-2xl bg-white shadow-sm ring-1 ring-slate-200">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4">
        <div className="flex items-center gap-3">
          <span className="grid size-9 place-items-center rounded-xl bg-slate-100 text-slate-600">
            <IconHistory size={18} />
          </span>
          <div>
            <p className="text-sm font-semibold text-slate-950">审计与案卷</p>
            <p className="mt-0.5 text-[11px] text-slate-500">
              执行记录与累计证据，默认收起，不打断当前任务。
            </p>
          </div>
        </div>
        <IconChevronDown
          size={17}
          className="text-slate-400 transition-transform group-open:rotate-180"
        />
      </summary>
      <div className="border-t border-slate-200 p-5">
        <EvidenceSummary item={item} />
        <div className="mt-5 border-t border-slate-200 pt-5">
          {item.events.length ? (
            <AgentTimeline item={item} />
          ) : (
            <p className="rounded-xl bg-slate-50 p-5 text-center text-sm text-slate-500">
              暂无执行记录。
            </p>
          )}
        </div>
      </div>
    </details>
  );
}

function CaseChat({ item }: { item: CraCase }) {
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatting, setChatting] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const wf = item.workflow;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: 'smooth',
      block: 'nearest',
    });
  }, [messages, chatting]);

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim()) return;
    const prompt = question.trim();
    const history = messages.map((message) => ({
      role: message.role,
      content: message.text,
    }));
    setMessages((current) => [
      ...current,
      { id: `user-${Date.now()}`, role: 'user', text: prompt },
    ]);
    setQuestion('');
    setChatting(true);
    try {
      const result = await craApi.chat(item.case_id, prompt, history);
      const sourceList = Array.isArray(result.sources)
        ? result.sources.map((source) => source.citation || '').filter(Boolean)
        : Object.keys(result.sources || {});
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          text: result.answer,
          sources: sourceList,
        },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: `assistant-error-${Date.now()}`,
          role: 'assistant',
          text:
            error instanceof Error
              ? error.message
              : '暂时无法回答，请稍后重试。',
        },
      ]);
    } finally {
      setChatting(false);
    }
  };

  return (
    <aside className="h-[620px] min-h-0 xl:sticky xl:top-5 xl:h-[calc(100vh-2.5rem)]">
      <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl bg-white shadow-[0_18px_50px_-32px_rgba(15,23,42,.45)] ring-1 ring-slate-200">
        <header className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-4">
          <div className="flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-xl bg-blue-50 text-blue-700">
              <IconRobot size={19} />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-slate-950">案件助手</h2>
              <p className="mt-0.5 text-[11px] text-slate-500">
                始终基于本案材料与已授权 RAG
              </p>
            </div>
          </div>
          <span className="flex items-center gap-1.5 text-[11px] text-slate-500">
            <span className="size-1.5 rounded-full bg-emerald-500" />
            在线
          </span>
        </header>
        <div
          aria-live="polite"
          className="min-h-0 flex-1 space-y-4 overflow-y-auto bg-slate-50/70 px-4 py-5 [scrollbar-color:#cbd5e1_transparent] [scrollbar-width:thin]"
        >
          <div className="flex items-start gap-2.5">
            <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-blue-700 text-white">
              <IconRobot size={15} />
            </span>
            <div className="max-w-[86%] rounded-2xl rounded-tl-md bg-white px-3.5 py-3 text-xs leading-5 text-slate-700 ring-1 ring-slate-200">
              {wf.status === 'blocked'
                ? `当前流程因「${wf.blocker || '材料不完整'}」暂停。你可以问我阻断原因、已有证据或下一步。`
                : `我正在跟进「${wf.current_task}」。你可以让我解释当前结果、证据或规则依据。`}
            </div>
          </div>
          {messages.map((message) =>
            message.role === 'user' ? (
              <div key={message.id} className="flex justify-end">
                <div className="max-w-[86%] rounded-2xl rounded-tr-md bg-blue-700 px-3.5 py-3 text-xs leading-5 text-white">
                  <p className="whitespace-pre-wrap">{message.text}</p>
                </div>
              </div>
            ) : (
              <div key={message.id} className="flex items-start gap-2.5">
                <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-blue-700 text-white">
                  <IconRobot size={15} />
                </span>
                <div className="max-w-[86%] rounded-2xl rounded-tl-md bg-white px-3.5 py-3 text-xs leading-5 text-slate-700 ring-1 ring-slate-200">
                  <p className="whitespace-pre-wrap">{message.text}</p>
                  {!!message.sources?.length && (
                    <div className="mt-3 border-t border-slate-100 pt-2">
                      <p className="mb-1.5 text-[10px] text-slate-400">
                        回答依据
                      </p>
                      {message.sources.map((source) => (
                        <p
                          key={source}
                          className="mt-1 text-[10px] leading-4 text-slate-500"
                        >
                          · {source}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )
          )}
          {chatting && (
            <div className="flex items-start gap-2.5">
              <span className="grid size-7 place-items-center rounded-lg bg-blue-700 text-white">
                <IconRobot size={15} />
              </span>
              <div className="flex gap-1 rounded-2xl bg-white px-4 py-3 ring-1 ring-slate-200">
                {[0, 1, 2].map((dot) => (
                  <span
                    key={dot}
                    className="size-1.5 animate-pulse rounded-full bg-slate-400"
                    style={{ animationDelay: `${dot * 140}ms` }}
                  />
                ))}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
        <div className="shrink-0 border-t border-slate-200 bg-white p-3">
          {!messages.length && (
            <div className="mb-3 flex gap-2 overflow-x-auto [scrollbar-width:none]">
              {[
                '为什么停在这里？',
                '我需要确认什么？',
                '用了哪些 RAG 资料？',
              ].map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => setQuestion(suggestion)}
                  className="shrink-0 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          )}
          <form onSubmit={ask} className="flex items-end gap-2">
            <Textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === 'Enter' &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              aria-label="输入案件问题"
              placeholder="输入案件问题…"
              className="max-h-28 min-h-11 resize-none !border-slate-300 !bg-white py-2.5 text-sm !text-slate-950 caret-blue-600 placeholder:!text-slate-400 focus-visible:!border-blue-500 focus-visible:!ring-blue-500/20"
            />
            <Button
              type="submit"
              size="icon"
              disabled={chatting || !question.trim()}
              aria-label="发送问题"
              className="size-11 shrink-0 rounded-xl bg-blue-700 hover:bg-blue-800"
            >
              {chatting ? (
                <IconLoader2 className="animate-spin" />
              ) : (
                <IconSend />
              )}
            </Button>
          </form>
          <p className="mt-2 px-1 text-[10px] text-slate-400">
            Enter 发送 · 重要结论请核对原始证据
          </p>
        </div>
      </section>
    </aside>
  );
}

export function CaseDetail({ caseId }: { caseId: string }) {
  const [item, setItem] = useState<CraCase | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState('');
  const [selectedEntityId, setSelectedEntityId] = useState('');
  const [selectedStage, setSelectedStage] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      setItem(await craApi.case(caseId));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '无法读取案件');
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!item) return;
    const candidates = (item.notes.entity_candidates || []) as Candidate[];
    const selected = item.notes.selected_entity as Candidate | undefined;
    setSelectedEntityId(
      (current) =>
        selected?.entity_id || current || candidates[0]?.entity_id || ''
    );
  }, [item]);
  useEffect(() => {
    if (item) setSelectedStage(visibleStage(item.workflow.current_stage));
  }, [item]);

  const progress = useMemo(
    () =>
      item
        ? Math.min(
            100,
            Math.round(
              (item.workflow.stage_index /
                Math.max(item.workflow.total_stages, 1)) *
                100
            )
          )
        : 0,
    [item]
  );

  const run = async () => {
    if (!item) return;
    setActing(true);
    setError('');
    try {
      setItem(await craApi.run(item.case_id));
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : 'Agent 执行失败');
    } finally {
      setActing(false);
    }
  };

  const resolve = async (
    task: HumanTask,
    resolution: Record<string, unknown> = {}
  ) => {
    if (!item) return;
    setActing(true);
    setError('');
    try {
      setItem(await craApi.resolve(item.case_id, task.id, resolution));
    } catch (resolveError) {
      setError(
        resolveError instanceof Error ? resolveError.message : '提交失败'
      );
    } finally {
      setActing(false);
    }
  };

  const loadDemoFinancials = async () => {
    if (!item) return;
    setActing(true);
    setError('');
    try {
      setItem(await craApi.useDemoFinancials(item.case_id));
    } catch (demoError) {
      setError(
        demoError instanceof Error ? demoError.message : '无法载入演示财报'
      );
    } finally {
      setActing(false);
    }
  };

  const uploadFinancialReport = async (file: File) => {
    if (!item) return false;
    setActing(true);
    setError('');
    try {
      const updated = await craApi.uploadFinancialReport(item.case_id, file);
      setItem(updated);
      setSelectedStage('financial_evidence');
      toast.success('财报已完成抽取', {
        description: `已进入财务证据签署，共 ${((updated.notes.financial_values || []) as unknown[]).length} 个字段。`,
      });
      return true;
    } catch (uploadError) {
      const message =
        uploadError instanceof Error ? uploadError.message : '财报上传失败';
      setError(message);
      toast.error('财报上传失败', { description: message });
      return false;
    } finally {
      setActing(false);
    }
  };

  const saveRiskOpinion = async (opinion: Record<string, unknown>) => {
    if (!item) return;
    setActing(true);
    setError('');
    try {
      setItem(
        await craApi.saveRiskOpinion(
          item.case_id,
          opinion as {
            company_description: string;
            risk_comments: string;
            stance?: string;
            qualitative?: Record<string, { choice: string; reason: string }>;
          }
        )
      );
    } catch (saveError) {
      setError(
        saveError instanceof Error ? saveError.message : '风险意见保存失败'
      );
    } finally {
      setActing(false);
    }
  };

  const regenerateRiskOpinion = async () => {
    if (!item) return;
    setActing(true);
    setError('');
    try {
      setItem(await craApi.regenerateRiskOpinion(item.case_id));
    } catch (regenerateError) {
      setError(
        regenerateError instanceof Error
          ? regenerateError.message
          : '风险意见起草失败'
      );
    } finally {
      setActing(false);
    }
  };

  if (loading)
    return (
      <div className="mx-auto max-w-[1600px] space-y-5 px-4 py-8 md:px-7">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-32 w-full rounded-2xl" />
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
          <Skeleton className="h-[520px] rounded-2xl" />
          <Skeleton className="h-[620px] rounded-2xl" />
        </div>
      </div>
    );
  if (!item)
    return (
      <div className="grid min-h-[70vh] place-items-center p-8 text-center">
        <div>
          <IconAlertTriangle className="mx-auto text-red-500" />
          <h1 className="mt-3 font-semibold">案件无法打开</h1>
          <p className="mt-2 text-sm text-slate-500">{error}</p>
          <Button asChild variant="outline" className="mt-5">
            <Link href="/workspace">返回工作台</Link>
          </Button>
        </div>
      </div>
    );

  const wf = item.workflow;
  const task = item.human_tasks[0];
  const needsFinancialReport = wf.current_stage === 'financial_evidence';
  const currentVisibleStage = visibleStage(wf.current_stage);
  const viewedStage = selectedStage || currentVisibleStage;
  const viewingHistory = viewedStage !== currentVisibleStage;
  const stageSnapshot = (item.stage_history || [])
    .filter((snapshot) =>
      (stageAliases[viewedStage] || [viewedStage]).includes(snapshot.stage)
    )
    .slice(-1)[0];
  const reliableStageSnapshot =
    stageSnapshot &&
    stageSnapshot.payload.notes &&
    stageSnapshot.payload.history_status !== 'reconstructed' &&
    stageSnapshot.title !== `${stageSnapshot.stage} 已完成`
      ? stageSnapshot
      : undefined;

  let workArea = null;
  if (task?.task_type === 'entity_confirmation') {
    workArea = (
      <EntityReview
        item={item}
        task={task}
        acting={acting}
        selectedId={selectedEntityId}
        onSelect={setSelectedEntityId}
        onConfirm={() =>
          void resolve(task, { selected_entity_id: selectedEntityId })
        }
      />
    );
  } else if (task?.task_type === 'financial_signoff') {
    workArea = (
      <FinancialReview
        item={item}
        acting={acting}
        onConfirm={() =>
          void resolve(task, {
            confirmed_fields: ((item.notes.financial_values || []) as unknown[])
              .length,
          })
        }
      />
    );
  } else if (task?.task_type === 'risk_signoff') {
    workArea = (
      <RiskReview
        item={item}
        acting={acting}
        editable
        signable
        onSave={(opinion) => void saveRiskOpinion(opinion)}
        onRegenerate={() => void regenerateRiskOpinion()}
        onConfirm={(opinion) => void resolve(task, { opinion })}
      />
    );
  } else if (
    wf.current_stage === 'write_workbook' ||
    wf.current_stage === 'final_signoff'
  ) {
    workArea = (
      <>
        <RiskReview
          item={item}
          acting={acting}
          editable
          signable={false}
          onSave={(opinion) => void saveRiskOpinion(opinion)}
          onRegenerate={() => void regenerateRiskOpinion()}
          onConfirm={() => undefined}
        />
        <WorkbookReview
          item={item}
          task={task}
          acting={acting}
          onRun={() => void run()}
          onConfirm={() =>
            task &&
            void resolve(task, { export_file: item.notes.export_file_name })
          }
        />
      </>
    );
  } else if (needsFinancialReport) {
    workArea = (
      <BlockedFinancials
        item={item}
        acting={acting}
        onUpload={uploadFinancialReport}
        onRun={() => void run()}
        onDemo={() => void loadDemoFinancials()}
      />
    );
  } else if (wf.status === 'running' || wf.status === 'ready') {
    workArea = (
      <RunningStep item={item} acting={acting} onRun={() => void run()} />
    );
  } else if (wf.status === 'completed') {
    const exportAvailable = Boolean(item.notes.export_path);
    workArea = (
      <>
        <RiskReview
          item={item}
          acting={acting}
          editable={false}
          signable={false}
          onSave={() => undefined}
          onRegenerate={() => undefined}
          onConfirm={() => undefined}
        />
        <section className="rounded-2xl bg-emerald-50 p-6 ring-1 ring-emerald-200">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <span className="grid size-10 place-items-center rounded-full bg-emerald-600 text-white">
                <IconCheck size={21} />
              </span>
              <div>
                <p className="font-semibold text-emerald-950">
                  案件已完成并归档
                </p>
                <p className="mt-1 text-xs text-emerald-800">
                  正式文件、人工签署、证据与 Agent 执行日志已关联。
                </p>
              </div>
            </div>
            {exportAvailable ? (
              <DownloadButton
                path={`/cases/${item.case_id}/export`}
                filename={String(
                  item.notes.export_file_name ||
                    `${item.case_id}_CRA_review.xlsx`
                )}
                label="下载最终版"
                recovery="交付文件可能已被移动或删除，请重新生成后再下载。"
                className="rounded-xl bg-white text-emerald-800"
              />
            ) : (
              <div
                role="status"
                className="flex max-w-sm items-start gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-900 ring-1 ring-amber-200"
              >
                <IconAlertTriangle size={16} className="mt-0.5 shrink-0" />
                <span>
                  <strong className="font-semibold">交付文件缺失。</strong>{' '}
                  请重新生成 CRA Excel 后再下载。
                </span>
              </div>
            )}
          </div>
        </section>
      </>
    );
  }

  if (viewingHistory) {
    workArea = (
      <CompletedStageView
        item={item}
        stage={viewedStage}
        snapshot={reliableStageSnapshot}
      />
    );
  }

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-7 md:px-7 md:py-9">
      <Link
        href="/workspace"
        className="mb-5 inline-flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-slate-900"
      >
        <IconArrowLeft size={17} /> 返回案件工作台
      </Link>
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-[-0.03em] md:text-3xl">
              {item.title}
            </h1>
            <span
              className={`rounded-md px-2 py-1 text-[11px] font-semibold ${statusTone[wf.status]}`}
            >
              {statusLabels[wf.status]}
            </span>
          </div>
          <p className="mt-2 text-sm text-slate-500">
            {item.case_id} · BP {item.bp} · {item.customer.name} ·{' '}
            {item.credit_segment}
          </p>
        </div>
        <button
          onClick={() => void load()}
          className="grid size-10 place-items-center rounded-xl bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
          aria-label="刷新案件"
        >
          <IconRefresh size={18} />
        </button>
      </div>

      {error && (
        <div
          role="alert"
          className="mt-5 flex gap-3 rounded-xl bg-red-50 p-4 text-sm text-red-800 ring-1 ring-red-200"
        >
          <IconAlertTriangle size={19} className="shrink-0" />
          {error}
        </div>
      )}

      <section className="mt-7 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200 md:p-6">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-xs font-medium text-slate-500">当前任务</p>
            <div className="mt-2 flex items-center gap-2">
              <span
                className={`size-2 rounded-full ${wf.status === 'running' ? 'animate-pulse bg-blue-600' : wf.status === 'blocked' ? 'bg-red-500' : wf.status === 'completed' ? 'bg-emerald-500' : 'bg-amber-500'}`}
              />
              <h2 className="text-base font-semibold">{wf.current_task}</h2>
            </div>
          </div>
          <div className="min-w-[280px] lg:w-[420px]">
            <div className="mb-2 flex items-center justify-between text-xs">
              <span className="text-slate-500">总流程进度</span>
              <span className="font-semibold tabular-nums">{progress}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-200">
              <div
                className="h-full rounded-full bg-blue-600 transition-[width] duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        </div>
        <div className="mt-6 grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
          {visibleStages.map((stage, index) => {
            const active = viewedStage === stage;
            const current = currentVisibleStage === stage;
            const sourceIndex = wf.stages.indexOf(stage);
            const done = wf.stage_index > sourceIndex;
            const available = done || current;
            return (
              <button
                key={stage}
                type="button"
                disabled={!available}
                aria-current={current ? 'step' : undefined}
                aria-pressed={active}
                onClick={() => setSelectedStage(stage)}
                className={`min-h-20 rounded-xl px-3 py-3 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-blue-600 disabled:cursor-not-allowed ${
                  active
                    ? 'bg-blue-50 text-blue-800 ring-1 ring-blue-200'
                    : done
                      ? 'bg-emerald-50 text-emerald-800 hover:bg-emerald-100'
                      : current
                        ? 'bg-blue-50/50 text-blue-800 hover:bg-blue-50'
                        : 'bg-slate-50 text-slate-400'
                }`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-[10px] tabular-nums opacity-70">
                    0{index + 1}
                  </span>
                  {active && done ? (
                    <IconEye size={14} aria-hidden="true" />
                  ) : done ? (
                    <IconCheck size={14} aria-hidden="true" />
                  ) : null}
                </span>
                <span className="mt-1 block text-xs font-semibold">
                  {stageLabels[stage]}
                </span>
                <span className="mt-1 block text-[10px] opacity-70">
                  {current ? '当前步骤' : done ? '点击回看' : '尚未开始'}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <div className="mt-6 grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <main className="min-w-0 space-y-5">
          {viewingHistory && (
            <div className="flex flex-col gap-3 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-950 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3">
                <IconHistory size={19} className="mt-0.5 shrink-0" />
                <div>
                  <p className="font-semibold">
                    正在回看：{stageLabels[viewedStage]}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-blue-800">
                    {reliableStageSnapshot
                      ? '展示该步骤完成时保存的结果；回看不会改变当前流程状态。'
                      : '该旧步骤没有当时保存的可验证快照，因此不展示当前数据。'}
                  </p>
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={() => setSelectedStage(currentVisibleStage)}
                className="shrink-0 border-blue-200 bg-white text-blue-800 hover:bg-blue-100"
              >
                返回当前步骤
              </Button>
            </div>
          )}
          {workArea}
          <AuditCaseFile item={item} />
        </main>
        <CaseChat item={item} />
      </div>
    </div>
  );
}
