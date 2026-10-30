import { MetadataRoute } from 'next';

import { envConfigs } from '@/config';

export default function robots(): MetadataRoute.Robots {
  const appUrl = envConfigs.app_url;

  return {
    rules: {
      userAgent: '*',
      allow: '/',
      disallow: [
        '/*?*q=',
        '/settings/*',
        '/admin/*',
        '/api/*',
      ],
    },
    sitemap: `${appUrl}/sitemap.xml`,
  };
}

