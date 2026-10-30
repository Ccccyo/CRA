import { NewCaseForm } from '@/features/cra/new-case-form';

import { redirect } from '@/core/i18n/navigation';
import {
  isMaintenanceAdministrator,
  PERMISSIONS,
  requirePermission,
} from '@/core/rbac/permission';
import { getSignUser } from '@/shared/models/user';
import { getUserRoles } from '@/shared/services/rbac';

export default async function NewCasePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const user = await getSignUser();
  const roles = user ? await getUserRoles(user.id) : [];
  if (isMaintenanceAdministrator(roles)) {
    redirect({ href: '/workspace', locale });
  }
  await requirePermission({
    code: PERMISSIONS.CRA_CASES_WRITE,
    redirectUrl: '/no-permission',
    locale,
  });
  return <NewCaseForm />;
}
