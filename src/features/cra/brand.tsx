import { IconShieldCheck } from '@tabler/icons-react';

export function CraBrand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <span className="grid size-9 place-items-center rounded-xl bg-[#1746d1] text-white shadow-[0_6px_18px_-10px_rgba(23,70,209,.8)]">
        <IconShieldCheck size={21} stroke={1.8} />
      </span>
      {!compact && (
        <span className="leading-tight">
          <strong className="block text-sm font-semibold tracking-[-0.02em] text-slate-950">
            CRA Agent
          </strong>
          <span className="text-[11px] text-slate-500">企业信用评审工作台</span>
        </span>
      )}
    </div>
  );
}

