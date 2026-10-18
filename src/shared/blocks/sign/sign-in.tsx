'use client';

import { useRef, useState } from 'react';
import {
  IconAlertCircle,
  IconArrowRight,
  IconCheck,
  IconEye,
  IconEyeOff,
  IconFileAnalytics,
  IconLoader2,
  IconLock,
  IconLogout,
} from '@tabler/icons-react';
import { useLocale, useTranslations } from 'next-intl';
import { toast } from 'sonner';

import { signIn, signOut } from '@/core/auth/client';
import { Link } from '@/core/i18n/navigation';
import { defaultLocale } from '@/config/locale';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Label } from '@/shared/components/ui/label';
import { cn } from '@/shared/lib/utils';

import { SocialProviders } from './social-providers';

type AuthError = {
  message?: string;
  code?: string;
  error?: { message?: string; code?: string; status?: number };
};

type SignedInWithoutAccess = {
  name: string;
  email: string;
};

type FieldErrors = {
  email?: string;
  password?: string;
};

const demoAccounts = [
  {
    email: 'admin@example.com',
    titleKey: 'demo_admin_title',
    permissionKey: 'demo_admin_permissions',
  },
  {
    email: 'analyst@example.com',
    titleKey: 'demo_analyst_title',
    permissionKey: 'demo_analyst_permissions',
  },
  {
    email: 'reviewer@example.com',
    titleKey: 'demo_reviewer_title',
    permissionKey: 'demo_reviewer_permissions',
  },
] as const;

export function SignIn({
  configs,
  callbackUrl = '/workspace',
  defaultEmail = '',
  signedInWithoutAccess,
}: {
  configs: Record<string, string>;
  callbackUrl: string;
  defaultEmail?: string;
  signedInWithoutAccess?: SignedInWithoutAccess;
}) {
  const locale = useLocale();
  const t = useTranslations('common.sign');
  const [email, setEmail] = useState(defaultEmail);
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [formError, setFormError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [blockedSession, setBlockedSession] = useState(signedInWithoutAccess);
  const [switchingAccount, setSwitchingAccount] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);
  const formErrorId = 'sign-in-error';
  const emailErrorId = 'sign-in-email-error';
  const passwordErrorId = 'sign-in-password-error';

  const isGoogleAuthEnabled = configs.google_auth_enabled === 'true';
  const isGithubAuthEnabled = configs.github_auth_enabled === 'true';
  const isEmailAuthEnabled =
    configs.email_auth_enabled !== 'false' ||
    (!isGoogleAuthEnabled && !isGithubAuthEnabled);
  const localizedCallbackUrl =
    locale !== defaultLocale &&
    callbackUrl.startsWith('/') &&
    !callbackUrl.startsWith('/' + locale)
      ? '/' + locale + callbackUrl
      : callbackUrl;
  const interactionBlocked = loading || switchingAccount || !!blockedSession;

  const reportError = (message: string, fields: FieldErrors = {}) => {
    setFormError(message);
    setFieldErrors(fields);
    toast.error(message);
  };

  const handleSignIn = async () => {
    if (interactionBlocked) return;

    const normalizedEmail = email.trim().toLowerCase();
    const isDemoAccount = demoAccounts.some(
      (account) => account.email === normalizedEmail
    );
    if (!normalizedEmail || !password) {
      reportError(t('sign_in_fields_required'), {
        email: !normalizedEmail ? t('email_required') : undefined,
        password: !password ? t('password_required') : undefined,
      });
      return;
    }

    setFormError('');
    setFieldErrors({});
    setLoading(true);

    try {
      await signIn.email(
        {
          email: normalizedEmail,
          password,
          callbackURL: localizedCallbackUrl,
        },
        {
          onSuccess: () => undefined,
          onError: (error) => {
            const authError = error as AuthError;
            const detail =
              (authError?.error?.code || authError?.code || '') +
              ' ' +
              (authError?.error?.message || authError?.message || '');
            const hasCredentialError =
              /invalid|credential|password|user not found/i.test(detail);
            const message = /load failed|failed to fetch|network|fetch/i.test(
              detail
            )
              ? t('sign_in_network_error')
              : hasCredentialError
                ? t(
                    isDemoAccount
                      ? 'demo_credentials_error'
                      : 'sign_in_credentials_error'
                  )
                : authError?.error?.message ||
                  authError?.message ||
                  t('sign_in_failed');
            reportError(
              message,
              hasCredentialError ? { password: message } : {}
            );
            setLoading(false);
          },
        }
      );
    } catch (error: unknown) {
      const authError = error as AuthError;
      const message = authError?.message || '';
      reportError(
        /load failed|failed to fetch|network|fetch/i.test(message)
          ? t('sign_in_network_error')
          : message || t('sign_in_failed')
      );
      setLoading(false);
    }
  };

  const handleSwitchAccount = () => {
    if (switchingAccount) return;
    setSwitchingAccount(true);
    setFormError('');
    setFieldErrors({});

    void signOut({
      fetchOptions: {
        onSuccess: () => {
          setBlockedSession(undefined);
          setSwitchingAccount(false);
        },
        onError: () => {
          setSwitchingAccount(false);
          reportError(t('switch_account_failed'));
        },
      },
    }).catch(() => {
      setSwitchingAccount(false);
      reportError(t('switch_account_failed'));
    });
  };

  const chooseDemoAccount = (accountEmail: string) => {
    setEmail(accountEmail);
    setFormError('');
    setFieldErrors({});
    window.requestAnimationFrame(() => passwordRef.current?.focus());
  };

  return (
    <div className="border-border bg-card text-card-foreground mx-auto grid w-full max-w-[1120px] overflow-hidden rounded-xl border lg:min-h-[640px] lg:grid-cols-[1.08fr_0.92fr]">
      <section className="bg-accent/55 order-2 flex flex-col px-6 py-8 md:px-9 lg:order-1 lg:px-12 lg:py-11">
        <div className="hidden lg:block">
          <h2 className="text-foreground max-w-[12ch] text-[2.5rem] leading-[1.12] font-semibold tracking-[-0.035em]">
            {t('workspace_sign_in_title')}
          </h2>
          <p className="text-muted-foreground mt-5 max-w-[56ch] text-sm leading-6">
            {t('workspace_sign_in_description')}
          </p>
          <div className="border-border text-foreground/75 mt-8 flex items-center gap-3 border-y py-4 text-xs font-medium">
            <IconFileAnalytics size={18} stroke={1.7} />
            <span>{t('workflow_summary')}</span>
          </div>
        </div>

        <div className="lg:mt-auto">
          {isEmailAuthEnabled ? (
            <>
              <div className="mb-4">
                <h3 className="text-foreground text-sm font-semibold">
                  {t('demo_accounts_title')}
                </h3>
                <p className="text-muted-foreground mt-1 text-xs leading-5">
                  {t('demo_accounts_description')}
                </p>
              </div>

              <div className="divide-border border-border divide-y border-y">
                {demoAccounts.map((account) => {
                  const selected = email.trim().toLowerCase() === account.email;
                  return (
                    <button
                      key={account.email}
                      type="button"
                      aria-pressed={selected}
                      disabled={interactionBlocked}
                      onClick={() => chooseDemoAccount(account.email)}
                      className="group hover:bg-card/70 focus-visible:bg-card focus-visible:ring-ring flex min-h-14 w-full items-center gap-4 py-3.5 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset disabled:cursor-not-allowed disabled:opacity-55"
                    >
                      <span
                        className={cn(
                          'grid size-8 shrink-0 place-items-center rounded-lg border text-xs font-semibold transition-colors',
                          selected
                            ? 'border-primary bg-primary text-primary-foreground'
                            : 'border-border bg-card text-muted-foreground'
                        )}
                      >
                        {selected ? (
                          <IconCheck size={16} />
                        ) : (
                          account.email[0].toUpperCase()
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-baseline gap-x-2">
                          <strong className="text-foreground text-sm font-semibold">
                            {t(account.titleKey)}
                          </strong>
                          <span className="text-muted-foreground text-xs">
                            {account.email}
                          </span>
                        </span>
                        <span className="text-foreground/75 mt-0.5 block text-xs leading-5">
                          {t(account.permissionKey)}
                        </span>
                      </span>
                      <IconArrowRight
                        size={17}
                        stroke={1.7}
                        className="text-muted-foreground group-hover:text-primary shrink-0 transition-transform group-hover:translate-x-0.5"
                      />
                    </button>
                  );
                })}
              </div>

              <p className="text-muted-foreground mt-4 flex items-start gap-2 text-xs leading-5">
                <IconLock size={15} stroke={1.8} className="mt-0.5 shrink-0" />
                {t('permissions_follow_account')}
              </p>
            </>
          ) : (
            <p className="border-border text-muted-foreground border-y py-4 text-xs leading-5">
              {t('email_auth_unavailable')}
            </p>
          )}
        </div>
      </section>

      <section className="order-1 flex items-center px-6 py-10 md:px-12 lg:order-2 lg:px-14">
        <div className="w-full">
          <div className="mb-8">
            <h1 className="text-foreground text-3xl font-semibold tracking-[-0.03em]">
              {t('sign_in_heading')}
            </h1>
            <p className="text-muted-foreground mt-3 text-sm leading-6">
              {t('sign_in_description')}
            </p>
          </div>

          <div className="grid gap-5">
            {blockedSession && (
              <div className="border-destructive/25 bg-destructive/10 rounded-lg border p-4">
                <div className="text-foreground flex items-start gap-3 text-sm">
                  <IconAlertCircle
                    size={19}
                    className="text-destructive mt-0.5 shrink-0"
                  />
                  <p className="leading-5">
                    {t('session_without_access', {
                      email: blockedSession.email,
                    })}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  className="mt-4 min-h-11 w-full"
                  disabled={switchingAccount}
                  onClick={handleSwitchAccount}
                >
                  {switchingAccount ? (
                    <IconLoader2 size={17} className="animate-spin" />
                  ) : (
                    <IconLogout size={17} />
                  )}
                  {switchingAccount
                    ? t('switching_account')
                    : t('switch_account')}
                </Button>
              </div>
            )}

            {formError && (
              <div
                id={formErrorId}
                role="alert"
                aria-live="polite"
                className="border-destructive/25 bg-destructive/10 text-foreground flex items-start gap-3 rounded-lg border p-3.5 text-sm"
              >
                <IconAlertCircle
                  size={18}
                  className="text-destructive mt-0.5 shrink-0"
                />
                <span className="leading-5">{formError}</span>
              </div>
            )}

            {isEmailAuthEnabled && (
              <form
                className="grid gap-5"
                onSubmit={(event) => {
                  event.preventDefault();
                  void handleSignIn();
                }}
              >
                <div className="grid gap-2">
                  <Label htmlFor="email">{t('email_title')}</Label>
                  <Input
                    id="email"
                    type="email"
                    placeholder={t('email_placeholder')}
                    autoComplete="email"
                    required
                    disabled={interactionBlocked}
                    aria-invalid={fieldErrors.email ? true : undefined}
                    aria-describedby={
                      fieldErrors.email ? emailErrorId : undefined
                    }
                    onChange={(event) => {
                      setEmail(event.target.value);
                      setFormError('');
                      setFieldErrors((errors) => ({
                        ...errors,
                        email: undefined,
                      }));
                    }}
                    value={email}
                    className="border-input bg-background focus-visible:border-primary focus-visible:ring-ring/20 h-12 rounded-lg px-3.5 text-base md:text-sm"
                  />
                  {fieldErrors.email && (
                    <p
                      id={emailErrorId}
                      className="text-destructive text-xs leading-5"
                    >
                      {fieldErrors.email}
                    </p>
                  )}
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="password">{t('password_title')}</Label>
                  <div className="relative">
                    <Input
                      ref={passwordRef}
                      id="password"
                      type={showPassword ? 'text' : 'password'}
                      placeholder={t('password_placeholder')}
                      autoComplete="current-password"
                      value={password}
                      required
                      disabled={interactionBlocked}
                      aria-invalid={fieldErrors.password ? true : undefined}
                      aria-describedby={
                        fieldErrors.password ? passwordErrorId : undefined
                      }
                      onChange={(event) => {
                        setPassword(event.target.value);
                        setFormError('');
                        setFieldErrors((errors) => ({
                          ...errors,
                          password: undefined,
                        }));
                      }}
                      className="border-input bg-background focus-visible:border-primary focus-visible:ring-ring/20 h-12 rounded-lg px-3.5 pr-12 text-base md:text-sm"
                    />
                    <button
                      type="button"
                      disabled={interactionBlocked}
                      onClick={() => setShowPassword((value) => !value)}
                      className="text-muted-foreground hover:text-primary focus-visible:ring-ring absolute inset-y-0 right-0 grid w-12 place-items-center rounded-r-lg outline-none focus-visible:ring-2 focus-visible:ring-inset disabled:opacity-50"
                      aria-label={
                        showPassword ? t('hide_password') : t('show_password')
                      }
                    >
                      {showPassword ? (
                        <IconEyeOff size={19} stroke={1.7} />
                      ) : (
                        <IconEye size={19} stroke={1.7} />
                      )}
                    </button>
                  </div>
                  {fieldErrors.password && (
                    <p
                      id={passwordErrorId}
                      className="text-destructive text-xs leading-5"
                    >
                      {fieldErrors.password}
                    </p>
                  )}
                </div>

                <Button
                  type="submit"
                  className="h-12 w-full rounded-lg text-sm font-semibold shadow-none"
                  disabled={interactionBlocked}
                >
                  {loading ? (
                    <>
                      <IconLoader2 size={17} className="animate-spin" />
                      <span>{t('signing_in')}</span>
                    </>
                  ) : (
                    <>
                      <span>{t('enter_workspace')}</span>
                      <IconArrowRight size={17} stroke={1.8} />
                    </>
                  )}
                </Button>
              </form>
            )}

            {!blockedSession && (
              <SocialProviders
                configs={configs}
                callbackUrl={localizedCallbackUrl}
                loading={loading}
                setLoading={setLoading}
              />
            )}

            {isEmailAuthEnabled && (
              <div className="border-border border-t pt-5">
                <p className="text-muted-foreground text-center text-xs">
                  {t('no_account')}
                  <Link
                    href="/sign-up"
                    className="text-primary decoration-primary/35 hover:decoration-primary ml-1 font-medium underline underline-offset-4"
                  >
                    {t('sign_up_title')}
                  </Link>
                </p>
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
