import Link from 'next/link';
import {
  IconArrowRight,
  IconCheck,
  IconFileSpreadsheet,
  IconLock,
  IconPlayerPlay,
  IconQuote,
  IconSearch,
  IconShieldCheck,
  IconSparkles,
} from '@tabler/icons-react';

import { CraBrand } from './brand';

function ProductPreview() {
  return (
    <div className="relative mx-auto w-full max-w-[610px] overflow-hidden rounded-2xl bg-white shadow-[0_34px_90px_-42px_rgba(15,23,42,.55)] ring-1 ring-slate-200">
      <div className="flex h-11 items-center justify-between border-b border-slate-200 px-4">
        <div className="flex gap-1.5">
          <span className="size-2 rounded-full bg-slate-300" />
          <span className="size-2 rounded-full bg-slate-300" />
          <span className="size-2 rounded-full bg-slate-300" />
        </div>
        <span className="text-[10px] font-medium text-slate-400">CASE-004</span>
      </div>
      <div className="grid min-h-[390px] grid-cols-[145px_1fr] max-sm:grid-cols-1">
        <div className="border-r border-slate-200 bg-slate-50 p-4 max-sm:hidden">
          <div className="mb-5 h-5 w-20 rounded bg-slate-200" />
          {['案件概览', '财务证据', '风险意见', '审批记录'].map((item, index) => (
            <div
              key={item}
              className={`mb-1.5 rounded-lg px-2.5 py-2 text-[10px] font-medium ${index === 0 ? 'bg-blue-50 text-blue-700' : 'text-slate-500'}`}
            >
              {item}
            </div>
          ))}
        </div>
        <div className="p-5 sm:p-6">
          <div className="mb-5 flex items-start justify-between gap-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-950">Northstar Trading 年度复审</h3>
              <p className="mt-1 text-[10px] text-slate-500">BP 1000201 · US · 申请额度 $2,500,000</p>
            </div>
            <span className="rounded-md bg-amber-50 px-2 py-1 text-[10px] font-semibold text-amber-800">等待人工</span>
          </div>
          <div className="mb-6 flex items-center gap-2">
            {[0, 1, 2, 3, 4].map((step) => (
              <span key={step} className={`h-1.5 flex-1 rounded-full ${step < 3 ? 'bg-blue-600' : 'bg-slate-200'}`} />
            ))}
          </div>
          <div className="rounded-xl bg-[#f7f9fc] p-4 ring-1 ring-slate-200">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-semibold text-slate-900">
                <span className="relative flex size-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-60" />
                  <span className="relative inline-flex size-2 rounded-full bg-blue-600" />
                </span>
                Agent 正在检索当前规则
              </div>
              <span className="text-[10px] tabular-nums text-slate-500">3 / 4</span>
            </div>
            <p className="text-[11px] leading-5 text-slate-600">
              正在比对额度方法、国家风险和同行业历史案件，为每个结论保留出处。
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {['信用政策 v3.2', 'OECD 国家风险', '历史案件 #108'].map((source) => (
                <span key={source} className="rounded-md bg-white px-2 py-1 text-[9px] text-slate-600 ring-1 ring-slate-200">
                  {source}
                </span>
              ))}
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200">
              <p className="text-[9px] text-slate-500">财务字段</p>
              <p className="mt-1 text-base font-semibold tabular-nums text-slate-950">21 / 21</p>
              <p className="mt-1 flex items-center gap-1 text-[9px] text-emerald-700"><IconCheck size={11} /> 已校验原页</p>
            </div>
            <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200">
              <p className="text-[9px] text-slate-500">自动决策</p>
              <p className="mt-1 text-base font-semibold tabular-nums text-slate-950">0</p>
              <p className="mt-1 flex items-center gap-1 text-[9px] text-blue-700"><IconLock size={11} /> 关键点由人签署</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function CraLanding() {
  return (
    <div className="min-h-screen overflow-hidden bg-[#f7f9fc] text-slate-950">
      <header className="relative z-20 mx-auto flex h-20 max-w-7xl items-center justify-between px-5 md:px-8">
        <CraBrand />
        <nav className="hidden items-center gap-8 text-sm font-medium text-slate-600 md:flex" aria-label="主导航">
          <a href="#workflow" className="hover:text-slate-950">工作流</a>
          <a href="#control" className="hover:text-slate-950">风险控制</a>
          <a href="#evidence" className="hover:text-slate-950">证据能力</a>
        </nav>
        <Link
          href="/workspace"
          className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
        >
          进入工作台 <IconArrowRight size={17} />
        </Link>
      </header>

      <main>
        <section className="relative mx-auto grid min-h-[720px] max-w-7xl items-center gap-16 px-5 py-20 md:px-8 lg:grid-cols-[.9fr_1.1fr] lg:py-24">
          <div className="absolute top-[-180px] right-[-260px] size-[640px] rounded-full bg-blue-100/65 blur-3xl" aria-hidden="true" />
          <div className="relative z-10">
            <h1 className="max-w-[640px] text-balance text-[clamp(2.8rem,6vw,5.25rem)] leading-[.98] font-semibold tracking-[-0.04em] text-slate-950">
              让企业信用评审，
              <span className="text-blue-700">每一步都有证据</span>
            </h1>
            <p className="mt-7 max-w-xl text-lg leading-8 text-slate-600">
              从 CRA Excel 建案、主体核验、财务抽取到风险意见和回填，Agent 完成重复工作，分析师保留关键决策权。
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-3">
              <Link href="/workspace/cases/new" className="inline-flex h-12 items-center gap-2 rounded-xl bg-blue-700 px-5 text-sm font-semibold text-white shadow-[0_12px_30px_-16px_rgba(29,78,216,.8)] transition hover:bg-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2">
                <IconFileSpreadsheet size={19} /> 上传 Excel 新建案件
              </Link>
              <a href="#workflow" className="inline-flex h-12 items-center gap-2 rounded-xl px-5 text-sm font-semibold text-slate-700 hover:bg-white">
                <IconPlayerPlay size={18} /> 看完整路径
              </a>
            </div>
            <div className="mt-9 flex flex-wrap gap-x-6 gap-y-3 text-sm text-slate-600">
              {['Excel 交接不变', '结论可追溯', '4 个人工卡点'].map((item) => (
                <span key={item} className="flex items-center gap-2"><IconCheck size={17} className="text-emerald-600" /> {item}</span>
              ))}
            </div>
          </div>
          <div className="relative z-10 lg:translate-x-5">
            <ProductPreview />
          </div>
        </section>

        <section id="workflow" className="border-y border-slate-200 bg-white py-24">
          <div className="mx-auto max-w-7xl px-5 md:px-8">
            <div className="grid gap-12 lg:grid-cols-[.72fr_1.28fr]">
              <div>
                <h2 className="text-balance text-4xl leading-tight font-semibold tracking-[-0.035em] md:text-5xl">
                  一条真正可执行的评审链路
                </h2>
                <p className="mt-5 max-w-md text-base leading-7 text-slate-600">
                  不用改变 SAP 和 CRA Excel 的交付方式。Agent 在中间负责读、查、校验、起草，每一次人工确认都留下责任记录。
                </p>
              </div>
              <ol className="divide-y divide-slate-200 border-y border-slate-200">
                {([
                  ['建案', '读取 CRA Excel，校验 BP、年份、币种与 SAP 交接数据。', IconFileSpreadsheet],
                  ['证据', '核对企业主体，抽取 21 个财务字段，保留原页与单位。', IconSearch],
                  ['判断', '按当前制度、历史案件和公开资料起草风险意见。', IconSparkles],
                  ['交付', '通过人工卡点后生成新版 CRA Excel，并完整归档。', IconShieldCheck],
                ] as const).map(([title, body, Icon], index) => (
                  <li key={String(title)} className="grid grid-cols-[48px_1fr] gap-5 py-7">
                    <span className="grid size-11 place-items-center rounded-xl bg-slate-100 text-slate-700"><Icon size={21} stroke={1.7} /></span>
                    <div>
                      <div className="flex items-center gap-3">
                        <h3 className="font-semibold text-slate-950">{title}</h3>
                        <span className="text-xs tabular-nums text-slate-400">0{index + 1}</span>
                      </div>
                      <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">{body}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </section>

        <section id="control" className="bg-slate-950 py-24 text-white">
          <div className="mx-auto grid max-w-7xl gap-14 px-5 md:px-8 lg:grid-cols-2">
            <div>
              <h2 className="max-w-xl text-balance text-4xl leading-tight font-semibold tracking-[-0.035em] md:text-5xl">
                Agent 会停下来，而不是替你做决定
              </h2>
              <p className="mt-6 max-w-xl text-base leading-7 text-slate-300">
                企业合并、财务证据签署、风险意见、最终额度是四个必经人工卡点。任何高风险异常都会阻断流程，不会被“自动化”掩盖。
              </p>
            </div>
            <div className="space-y-0 border-y border-slate-700">
              {[
                ['企业主体', '防止同名公司被错误合并'],
                ['财务证据', '数值、单位和原页必须可核对'],
                ['风险意见', '每个结论必须能追回规则或案件材料'],
                ['最终额度', '偏离规则时必须说明理由并签署'],
              ].map(([title, body], index) => (
                <div key={title} className="grid grid-cols-[36px_1fr] gap-4 border-b border-slate-800 py-6 last:border-0">
                  <span className="pt-0.5 text-xs tabular-nums text-blue-300">0{index + 1}</span>
                  <div><h3 className="font-semibold">{title}</h3><p className="mt-1 text-sm leading-6 text-slate-400">{body}</p></div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="evidence" className="bg-white py-24">
          <div className="mx-auto max-w-5xl px-5 text-center md:px-8">
            <IconQuote size={28} className="mx-auto text-blue-700" />
            <blockquote className="mx-auto mt-6 max-w-4xl text-balance text-3xl leading-snug font-medium tracking-[-0.025em] text-slate-900 md:text-4xl">
              不是让 AI 把评审写得更快，而是让分析师在每个结论上都更快地找到“为什么”。
            </blockquote>
            <p className="mt-5 text-sm text-slate-500">CRA Agent 的产品原则</p>
            <Link href="/workspace" className="mt-10 inline-flex h-12 items-center gap-2 rounded-xl bg-slate-950 px-6 text-sm font-semibold text-white hover:bg-slate-800">
              打开演示工作台 <IconArrowRight size={18} />
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-5 py-8 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between md:px-8">
          <CraBrand />
          <p>演示数据已标记，生产环境需接入经授权的 SAP、D&amp;B 与制度库。</p>
        </div>
      </footer>
    </div>
  );
}

