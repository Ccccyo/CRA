import { envConfigs } from '..';

export const localeNames: any = {
  en: 'English',
  zh: '中文',
};

export const locales = ['en', 'zh'];

export const defaultLocale = envConfigs.locale;

// Next 16 reruns proxy rewrites; an explicit locale prefix avoids an
// as-needed rewrite/redirect loop while keeping both language routes valid.
export const localePrefix = 'always';

export const localeDetection = false;

export const localeMessagesRootPath = '@/config/locale/messages';

export const localeMessagesPaths = [
  'common',
  'settings/sidebar',
  'settings/profile',
  'settings/security',
  'admin/sidebar',
  'admin/users',
  'admin/roles',
  'admin/permissions',
];
