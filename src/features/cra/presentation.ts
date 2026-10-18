import type { WorkflowStatus } from './types';

export const stageLabels: Record<string, string> = {
  intake_excel: '读取 Excel',
  resolve_entity: '核对企业主体',
  financial_evidence: '抽取财务证据',
  financial_signoff: '签署财务证据',
  retrieve_context: '检索规则与历史',
  risk_signoff: '风险评测与意见',
  write_workbook: '生成 Excel 版本',
  external_score: '外部评分',
  final_signoff: '最终复核',
  completed: '已完成',
};

export const statusLabels: Record<WorkflowStatus, string> = {
  ready: '待开始',
  running: 'Agent 处理中',
  awaiting_human: '等待人工',
  blocked: '已阻断',
  completed: '已完成',
};

export const statusTone: Record<WorkflowStatus, string> = {
  ready: 'bg-slate-100 text-slate-700',
  running: 'bg-blue-50 text-blue-700',
  awaiting_human: 'bg-amber-50 text-amber-800',
  blocked: 'bg-red-50 text-red-700',
  completed: 'bg-emerald-50 text-emerald-700',
};

export function money(value: number) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value || 0);
}

export function dateTime(value?: string) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

