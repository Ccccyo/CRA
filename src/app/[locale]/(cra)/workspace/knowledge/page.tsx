import { KnowledgeBase } from '@/features/cra/knowledge-base';

import { PERMISSIONS, requirePermission } from '@/core/rbac/permission';

export default async function KnowledgePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  await requirePermission({
    code: PERMISSIONS.CRA_KNOWLEDGE_READ,
    redirectUrl: '/no-permission',
    locale,
  });
  return <KnowledgeBase />;
}
