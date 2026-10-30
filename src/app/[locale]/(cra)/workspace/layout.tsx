import { WorkspaceShell } from '@/features/cra/workspace-shell';

import {
  isMaintenanceAdministrator,
  PERMISSIONS,
  requirePermission,
} from '@/core/rbac/permission';
import { getSignUser } from '@/shared/models/user';
import { getUserRoles, hasPermission } from '@/shared/services/rbac';

export const dynamic = 'force-dynamic';

export default async function CraWorkspaceLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  await requirePermission({
    code: PERMISSIONS.CRA_WORKSPACE_ACCESS,
    redirectUrl: '/no-permission',
    locale,
  });
  const user = await getSignUser();
  const roles = user ? await getUserRoles(user.id) : [];
  const maintenanceOnly = isMaintenanceAdministrator(roles);
  const [canReadCases, canWriteCases, canReadKnowledge] = user
    ? await Promise.all([
        maintenanceOnly
          ? true
          : hasPermission(user.id, PERMISSIONS.CRA_CASES_READ),
        maintenanceOnly
          ? false
          : hasPermission(user.id, PERMISSIONS.CRA_CASES_WRITE),
        hasPermission(user.id, PERMISSIONS.CRA_KNOWLEDGE_READ),
      ])
    : [false, false, false];

  return (
    <WorkspaceShell
      currentUser={{
        name: user?.name || user?.email || '当前用户',
        role: roles[0]?.title || '未分配角色',
        canReadCases,
        canWriteCases,
        canReadKnowledge,
        maintenanceOnly,
      }}
    >
      {children}
    </WorkspaceShell>
  );
}
