import { getTranslations } from 'next-intl/server';

import { redirect } from '@/core/i18n/navigation';
import { PERMISSIONS } from '@/core/rbac/permission';
import { envConfigs } from '@/config';
import { defaultLocale } from '@/config/locale';
import { SignIn } from '@/shared/blocks/sign/sign-in';
import { getConfigs } from '@/shared/models/config';
import { getSignUser } from '@/shared/models/user';
import { hasPermission } from '@/shared/services/rbac';

function normalizeWorkspaceCallback(raw: string | undefined, locale: string) {
  const fallback = '/workspace';

  if (
    !raw ||
    !raw.startsWith('/') ||
    raw.startsWith('//') ||
    raw.includes('\\') ||
    /%(?:2f|5c)/i.test(raw) ||
    /[\u0000-\u001f\u007f]/.test(raw)
  ) {
    return fallback;
  }

  try {
    const origin = 'https://cra.local';
    const parsed = new URL(raw, origin);
    if (parsed.origin !== origin) return fallback;

    let pathname = parsed.pathname;
    if (locale !== defaultLocale) {
      if (pathname === `/${locale}`) pathname = '/';
      if (pathname.startsWith(`/${locale}/`)) {
        pathname = pathname.slice(locale.length + 1) || '/';
      }
    }

    if (pathname !== '/workspace' && !pathname.startsWith('/workspace/')) {
      return fallback;
    }

    return `${pathname}${parsed.search}`;
  } catch {
    return fallback;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;

  const t = await getTranslations('common');

  return {
    title: `${t('sign.sign_in_title')} - ${t('metadata.title')}`,
    alternates: {
      canonical:
        locale !== defaultLocale
          ? `${envConfigs.app_url}/${locale}/sign-in`
          : `${envConfigs.app_url}/sign-in`,
    },
  };
}

export default async function SignInPage({
  searchParams,
  params,
}: {
  searchParams: Promise<{
    callbackUrl?: string;
    email?: string;
    verified?: string;
  }>;
  params: Promise<{ locale: string }>;
}) {
  const { callbackUrl, email } = await searchParams;
  const { locale } = await params;
  const normalizedCallbackUrl = normalizeWorkspaceCallback(callbackUrl, locale);

  const sessionUser = await getSignUser();
  const canAccessWorkspace = sessionUser
    ? await hasPermission(sessionUser.id, PERMISSIONS.CRA_WORKSPACE_ACCESS)
    : false;

  if (sessionUser && canAccessWorkspace) {
    redirect({ href: normalizedCallbackUrl, locale });
  }

  const configs = await getConfigs();

  return (
    <SignIn
      configs={configs}
      callbackUrl={normalizedCallbackUrl}
      defaultEmail={email || ''}
      signedInWithoutAccess={
        sessionUser
          ? {
              name: sessionUser.name || sessionUser.email,
              email: sessionUser.email,
            }
          : undefined
      }
    />
  );
}
