import { MetadataRoute } from 'next';

import { envConfigs } from '@/config';

export default function sitemap(): MetadataRoute.Sitemap {
  const appUrl = envConfigs.app_url.replace(/\/$/, '');

  return [
    {
      url: `${appUrl}/`,
      changeFrequency: 'weekly',
      priority: 1,
    },
  ];
}
