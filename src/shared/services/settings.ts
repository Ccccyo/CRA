export interface Setting {
  name: string;
  title: string;
  type: string;
  placeholder?: string;
  options?: {
    title: string;
    value: string;
  }[];
  tip?: string;
  value?: string | string[] | boolean | number;
  group?: string;
  tab?: string;
  attributes?: Record<string, unknown>;
}

/**
 * Runtime-configurable settings used by the CRA workbench.
 *
 * Generic storefront, payment, content-generation, advertising, affiliate and
 * customer-service settings were intentionally removed with their template
 * modules. Keeping this list focused also prevents obsolete environment keys
 * from being loaded into the server configuration on every request.
 */
export async function getSettings(): Promise<Setting[]> {
  return [
    {
      name: 'app_name',
      title: 'App Name',
      placeholder: 'CRA Agent Workbench',
      type: 'text',
      group: 'appinfo',
      tab: 'general',
    },
    {
      name: 'app_description',
      title: 'App Description',
      placeholder:
        'Evidence-backed enterprise credit review workbench for CRA Excel intake, analysis and sign-off.',
      type: 'textarea',
      group: 'appinfo',
      tab: 'general',
    },
    {
      name: 'app_logo',
      title: 'App Logo',
      type: 'upload_image',
      group: 'appinfo',
      tab: 'general',
    },
    {
      name: 'app_preview_image',
      title: 'App Preview Image',
      type: 'upload_image',
      group: 'appinfo',
      tab: 'general',
    },
    {
      name: 'initial_role_enabled',
      title: 'Initial Role Enabled',
      type: 'switch',
      value: 'false',
      group: 'user_role',
      tab: 'general',
    },
    {
      name: 'initial_role_name',
      title: 'Initial Role',
      type: 'select',
      value: 'viewer',
      options: [
        { title: 'Viewer', value: 'viewer' },
        { title: 'Editor', value: 'editor' },
        { title: 'Admin', value: 'admin' },
        { title: 'Super Admin', value: 'super_admin' },
        { title: '信用分析师', value: 'credit_analyst' },
        { title: '高级信用分析师', value: 'senior_credit_analyst' },
        { title: '区域信用经理', value: 'credit_manager' },
        { title: '质量管理者', value: 'quality_manager' },
      ],
      group: 'user_role',
      tab: 'general',
    },
    {
      name: 'initial_credits_enabled',
      title: 'Initial Credits Enabled',
      type: 'switch',
      value: 'false',
      group: 'credit',
      tab: 'general',
    },
    {
      name: 'initial_credits_amount',
      title: 'Initial Credits Amount',
      type: 'number',
      placeholder: '0',
      group: 'credit',
      tab: 'general',
    },
    {
      name: 'initial_credits_valid_days',
      title: 'Initial Credits Valid Days',
      type: 'number',
      placeholder: '30',
      group: 'credit',
      tab: 'general',
    },
    {
      name: 'initial_credits_description',
      title: 'Initial Credits Description',
      type: 'text',
      placeholder: 'initial credits',
      group: 'credit',
      tab: 'general',
    },
    {
      name: 'email_auth_enabled',
      title: 'Email Authentication',
      type: 'switch',
      value: 'true',
      group: 'email_auth',
      tab: 'auth',
    },
    {
      name: 'google_auth_enabled',
      title: 'Google Authentication',
      type: 'switch',
      value: 'false',
      group: 'google_auth',
      tab: 'auth',
    },
    {
      name: 'google_one_tap_enabled',
      title: 'Google One Tap',
      type: 'switch',
      value: 'false',
      group: 'google_auth',
      tab: 'auth',
    },
    {
      name: 'google_client_id',
      title: 'Google Client ID',
      type: 'text',
      group: 'google_auth',
      tab: 'auth',
    },
    {
      name: 'google_client_secret',
      title: 'Google Client Secret',
      type: 'password',
      group: 'google_auth',
      tab: 'auth',
    },
    {
      name: 'github_auth_enabled',
      title: 'GitHub Authentication',
      type: 'switch',
      value: 'false',
      group: 'github_auth',
      tab: 'auth',
    },
    {
      name: 'github_client_id',
      title: 'GitHub Client ID',
      type: 'text',
      group: 'github_auth',
      tab: 'auth',
    },
    {
      name: 'github_client_secret',
      title: 'GitHub Client Secret',
      type: 'password',
      group: 'github_auth',
      tab: 'auth',
    },
    {
      name: 'resend_api_key',
      title: 'Resend API Key',
      type: 'password',
      group: 'resend',
      tab: 'email',
    },
    {
      name: 'resend_sender_email',
      title: 'Resend Sender Email',
      type: 'text',
      placeholder: 'CRA Agent Workbench <no-reply@cra.example.com>',
      group: 'resend',
      tab: 'email',
    },
    {
      name: 'r2_account_id',
      title: 'Cloudflare Account ID',
      type: 'text',
      group: 'r2',
      tab: 'storage',
    },
    {
      name: 'r2_access_key',
      title: 'Cloudflare Access Key',
      type: 'text',
      group: 'r2',
      tab: 'storage',
    },
    {
      name: 'r2_secret_key',
      title: 'Cloudflare Secret Key',
      type: 'password',
      group: 'r2',
      tab: 'storage',
    },
    {
      name: 'r2_bucket_name',
      title: 'Bucket Name',
      type: 'text',
      group: 'r2',
      tab: 'storage',
    },
    {
      name: 'r2_upload_path',
      title: 'Upload Path',
      type: 'text',
      placeholder: 'uploads',
      group: 'r2',
      tab: 'storage',
    },
    {
      name: 'r2_endpoint',
      title: 'Endpoint',
      type: 'url',
      placeholder: 'https://<account-id>.r2.cloudflarestorage.com',
      group: 'r2',
      tab: 'storage',
    },
    {
      name: 'r2_domain',
      title: 'Public Domain',
      type: 'url',
      group: 'r2',
      tab: 'storage',
    },
    {
      name: 'google_analytics_id',
      title: 'Google Analytics ID',
      type: 'text',
      group: 'google_analytics',
      tab: 'analytics',
    },
    {
      name: 'clarity_id',
      title: 'Microsoft Clarity ID',
      type: 'text',
      group: 'clarity',
      tab: 'analytics',
    },
    {
      name: 'plausible_domain',
      title: 'Plausible Domain',
      type: 'text',
      placeholder: 'cra.example.com',
      group: 'plausible',
      tab: 'analytics',
    },
    {
      name: 'plausible_src',
      title: 'Plausible Script URL',
      type: 'url',
      placeholder: 'https://plausible.io/js/script.js',
      group: 'plausible',
      tab: 'analytics',
    },
    {
      name: 'openpanel_client_id',
      title: 'OpenPanel Client ID',
      type: 'text',
      group: 'openpanel',
      tab: 'analytics',
    },
    {
      name: 'vercel_analytics_enabled',
      title: 'Vercel Analytics',
      type: 'switch',
      value: 'false',
      group: 'vercel_analytics',
      tab: 'analytics',
    },
  ];
}

export const publicSettingNames = [
  'email_auth_enabled',
  'google_auth_enabled',
  'google_one_tap_enabled',
  'google_client_id',
  'github_auth_enabled',
];

export async function getAllSettingNames() {
  const settings = await getSettings();
  return settings.map((setting) => setting.name);
}
