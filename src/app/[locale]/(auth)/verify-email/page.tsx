import { redirect } from '@/core/i18n/navigation';

export default async function RetiredVerifyEmailRoute({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect({ href: '/sign-in', locale });
}
