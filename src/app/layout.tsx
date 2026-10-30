import '@/config/style/global.css';

import { Inter, JetBrains_Mono, Merriweather } from 'next/font/google';
import { getLocale, setRequestLocale } from 'next-intl/server';
import NextTopLoader from 'nextjs-toploader';

import { envConfigs } from '@/config';
import { locales } from '@/config/locale';
import { getAllConfigs } from '@/shared/models/config';
import { getAnalyticsService } from '@/shared/services/analytics';

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-sans',
  display: 'swap',
  preload: true,
});

const merriweather = Merriweather({
  subsets: ['latin'],
  weight: ['400', '700'],
  variable: '--font-serif',
  display: 'swap',
  preload: true,
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
  preload: true,
});

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getLocale();
  setRequestLocale(locale);

  const isProduction = process.env.NODE_ENV === 'production';
  const isDebug = process.env.NEXT_PUBLIC_DEBUG === 'true';

  // app url
  const appUrl = envConfigs.app_url || '';

  // analytics components
  let analyticsMetaTags = null;
  let analyticsHeadScripts = null;
  let analyticsBodyScripts = null;

  if (isProduction || isDebug) {
    const configs = await getAllConfigs();

    const analyticsService = await getAnalyticsService(configs);

    // get analytics components
    analyticsMetaTags = analyticsService.getMetaTags();
    analyticsHeadScripts = analyticsService.getHeadScripts();
    analyticsBodyScripts = analyticsService.getBodyScripts();

  }

  return (
    <html
      lang={locale}
      className={`${inter.variable} ${merriweather.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <link rel="icon" href={envConfigs.app_favicon} />
        <link rel="alternate icon" href="/favicon.ico" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />

        {/* inject locales */}
        {locales ? (
          <>
            {locales.map((loc) => (
              <link
                key={loc}
                rel="alternate"
                hrefLang={loc}
                href={`${appUrl}${loc === 'en' ? '' : `/${loc}`}`}
              />
            ))}
          </>
        ) : null}

        {/* inject analytics meta tags */}
        {analyticsMetaTags}
        {/* inject analytics head scripts */}
        {analyticsHeadScripts}
      </head>
      <body suppressHydrationWarning className="overflow-x-hidden">
        <NextTopLoader
          color="#1746d1"
          initialPosition={0.08}
          crawlSpeed={200}
          height={3}
          crawl={true}
          showSpinner={true}
          easing="ease"
          speed={200}
        />

        {children}

        {/* inject analytics body scripts */}
        {analyticsBodyScripts}

      </body>
    </html>
  );
}
