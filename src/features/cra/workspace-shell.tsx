'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  IconBook2,
  IconFilePlus,
  IconLayoutDashboard,
  IconLoader2,
  IconLogout,
  IconMenu2,
  IconSearch,
  IconX,
} from '@tabler/icons-react';
import { toast } from 'sonner';

import { signOut } from '@/core/auth/client';
import { useRouter } from '@/core/i18n/navigation';

import { CraBrand } from './brand';

const caseNav = [
  {
    href: '/workspace',
    label: '案件工作台',
    icon: IconLayoutDashboard,
    exact: true,
  },
  { href: '/workspace/cases/new', label: '新建案件', icon: IconFilePlus },
];

const knowledgeNav = [
  { href: '/workspace/knowledge', label: '知识库', icon: IconBook2 },
];

function SidebarContent({
  permissions,
  onNavigate,
}: {
  permissions: {
    canReadCases: boolean;
    canWriteCases: boolean;
    canReadKnowledge: boolean;
    maintenanceOnly: boolean;
  };
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const normalized = pathname.replace(/^\/(zh|en)(?=\/|$)/, '') || '/';
  const navigationSections = [
    { label: '案件管理', items: caseNav },
    { label: '知识与规则', items: knowledgeNav },
  ]
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        if (item.href === '/workspace') return permissions.canReadCases;
        if (item.href === '/workspace/cases/new')
          return permissions.canWriteCases;
        return permissions.canReadKnowledge;
      }),
    }))
    .filter((section) => section.items.length > 0);
  return (
    <>
      <div className="flex h-20 items-center border-b border-slate-200 px-6">
        <CraBrand />
      </div>
      <nav className="flex-1 p-4" aria-label="主导航">
        {navigationSections.map((section, sectionIndex) => (
          <div
            key={section.label}
            className={
              sectionIndex ? 'mt-5 border-t border-slate-200 pt-5' : ''
            }
          >
            <p className="mb-2 px-3 text-[10px] font-semibold tracking-[0.12em] text-slate-400 uppercase">
              {section.label}
            </p>
            <div className="space-y-1.5">
              {section.items.map((item) => {
                const active =
                  item.href === '/workspace'
                    ? normalized === item.href
                    : normalized.startsWith(item.href);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onNavigate}
                    className={`flex h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none ${
                      active
                        ? item.href === '/workspace'
                          ? 'bg-blue-700 text-white shadow-sm'
                          : 'bg-blue-50 text-blue-800'
                        : 'text-slate-600 hover:bg-slate-100 hover:text-slate-950'
                    }`}
                  >
                    <Icon size={19} stroke={1.8} />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="border-t border-slate-200 p-4">
        <div className="rounded-xl bg-slate-100 p-3">
          <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-slate-800">
            <IconBook2 size={16} /> 资料访问
          </div>
          <p className="text-xs leading-5 text-slate-600">
            {permissions.maintenanceOnly
              ? '可查看全部案件并维护知识资料；不能创建、分析、编辑或签署案件。'
              : '知识资料按当前账号权限显示；管理操作仅对管理员开放。'}
          </p>
        </div>
      </div>
    </>
  );
}

export function WorkspaceShell({
  children,
  currentUser,
}: {
  children: React.ReactNode;
  currentUser: {
    name: string;
    role: string;
    canReadCases: boolean;
    canWriteCases: boolean;
    canReadKnowledge: boolean;
    maintenanceOnly: boolean;
  };
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const router = useRouter();

  const handleSignOut = () => {
    if (signingOut) return;
    setSigningOut(true);
    void signOut({
      fetchOptions: {
        onSuccess: () => {
          router.push('/sign-in');
          router.refresh();
        },
        onError: () => {
          setSigningOut(false);
          toast.error('退出失败，请稍后重试。');
        },
      },
    }).catch(() => {
      setSigningOut(false);
      toast.error('退出失败，请稍后重试。');
    });
  };

  return (
    <div className="min-h-screen bg-[#f5f7fb] text-slate-950">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-slate-200 bg-white lg:flex">
        <SidebarContent permissions={currentUser} />
      </aside>

      {menuOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            aria-label="关闭菜单"
            className="absolute inset-0 bg-slate-950/30"
            onClick={() => setMenuOpen(false)}
          />
          <aside className="relative flex h-full w-[min(84vw,300px)] flex-col bg-white shadow-2xl">
            <button
              className="absolute top-5 right-4 grid size-9 place-items-center rounded-lg text-slate-600 hover:bg-slate-100"
              onClick={() => setMenuOpen(false)}
              aria-label="关闭菜单"
            >
              <IconX size={20} />
            </button>
            <SidebarContent
              permissions={currentUser}
              onNavigate={() => setMenuOpen(false)}
            />
          </aside>
        </div>
      )}

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-4 backdrop-blur md:px-7">
          <div className="flex items-center gap-3">
            <button
              className="grid size-9 place-items-center rounded-lg text-slate-700 hover:bg-slate-100 lg:hidden"
              onClick={() => setMenuOpen(true)}
              aria-label="打开菜单"
            >
              <IconMenu2 size={21} />
            </button>
            {currentUser.canReadCases ? (
              <Link
                href="/workspace#case-search"
                aria-label="前往案件检索"
                className="hidden min-w-72 items-center gap-2 rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-500 transition-colors hover:bg-slate-200 hover:text-slate-800 md:flex"
              >
                <IconSearch size={17} />
                <span>前往案件检索</span>
              </Link>
            ) : (
              <span className="hidden items-center gap-2 text-sm font-medium text-slate-600 md:flex">
                <IconBook2 size={17} className="text-blue-700" />
                平台维护模式
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-3 border-l border-slate-200 pl-4 sm:flex">
              <span className="grid size-8 place-items-center rounded-full bg-slate-900 text-xs font-semibold text-white">
                {currentUser.name.slice(0, 2)}
              </span>
              <span className="leading-tight">
                <strong className="block max-w-36 truncate text-xs font-semibold">
                  {currentUser.name}
                </strong>
                <span className="block max-w-36 truncate text-[11px] text-slate-500">
                  {currentUser.role}
                </span>
              </span>
            </div>
            <button
              type="button"
              onClick={handleSignOut}
              disabled={signingOut}
              className="grid size-11 place-items-center rounded-lg text-slate-500 transition-colors outline-none hover:bg-slate-100 hover:text-slate-950 focus-visible:ring-2 focus-visible:ring-blue-600 disabled:cursor-wait disabled:opacity-60"
              aria-label="退出登录"
              title="退出登录并切换账号"
            >
              {signingOut ? (
                <IconLoader2 size={18} className="animate-spin" />
              ) : (
                <IconLogout size={18} stroke={1.8} />
              )}
            </button>
          </div>
        </header>
        <main>{children}</main>
      </div>
    </div>
  );
}
