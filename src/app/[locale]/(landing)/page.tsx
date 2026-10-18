import { setRequestLocale } from 'next-intl/server';

import { CraLanding } from '@/features/cra/landing';

export default async function LandingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return <CraLanding />;
}
