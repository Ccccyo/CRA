import { CaseList } from '@/features/cra/case-list';

import { redirect } from '@/core/i18n/navigation';
import {
  isMaintenanceAdministrator,
  PERMISSIONS,
} from '@/core/rbac/permission';
import { getSignUser } from '@/shared/models/user';
import { getUserRoles, hasPermission } from '@/shared/services/rbac';

export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const user = await getSignUser();
  const roles = user ? await getUserRoles(user.id) : [];
  const maintenanceOnly = isMaintenanceAdministrator(roles);
  const canReadCases = user
    ? maintenanceOnly ||
      (await hasPermission(user.id, PERMISSIONS.CRA_CASES_READ))
    : false;
  if (user && !canReadCases) {
    redirect({ href: '/workspace/knowledge', locale });
  }
  const canCreateCases = user
    ? !maintenanceOnly &&
      (await hasPermission(user.id, PERMISSIONS.CRA_CASES_WRITE))
    : false;
  return (
    <CaseList
      canCreateCases={canCreateCases}
      readOnlyOverview={maintenanceOnly}
    />
  );
}
