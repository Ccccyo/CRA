import { CaseDetail } from '@/features/cra/case-detail';

import { redirect } from '@/core/i18n/navigation';
import {
  isMaintenanceAdministrator,
  PERMISSIONS,
} from '@/core/rbac/permission';
import { getSignUser } from '@/shared/models/user';
import { getUserRoles, hasPermission } from '@/shared/services/rbac';

export default async function CaseDetailPage({
  params,
}: {
  params: Promise<{ locale: string; caseId: string }>;
}) {
  const { locale, caseId } = await params;
  const user = await getSignUser();
  const roles = user ? await getUserRoles(user.id) : [];
  const canReadCase = user
    ? isMaintenanceAdministrator(roles) ||
      (await hasPermission(user.id, PERMISSIONS.CRA_CASES_READ))
    : false;
  if (!canReadCase) redirect({ href: '/no-permission', locale });
  return <CaseDetail caseId={caseId} />;
}
