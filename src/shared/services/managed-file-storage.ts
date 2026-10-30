import { mkdir, stat, unlink } from 'node:fs/promises';
import path from 'node:path';
import { AwsClient } from 'aws4fetch';

export type ManagedStorageBackend = 'local' | 'r2';

export type ManagedStorageConfig = {
  backend: ManagedStorageBackend;
  bucket: string;
  endpoint: string;
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
  uploadTtlSeconds: number;
  downloadTtlSeconds: number;
};

function positiveInteger(value: string | undefined, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

export function managedStorageConfig(): ManagedStorageConfig {
  const accountId = process.env.FILE_STORAGE_ACCOUNT_ID || '';
  const accessKeyId =
    process.env.FILE_STORAGE_ACCESS_KEY_ID ||
    process.env.R2_ACCESS_KEY_ID ||
    '';
  const secretAccessKey =
    process.env.FILE_STORAGE_SECRET_ACCESS_KEY ||
    process.env.R2_SECRET_ACCESS_KEY ||
    '';
  const bucket =
    process.env.FILE_STORAGE_BUCKET || process.env.R2_BUCKET_NAME || '';
  const endpoint =
    process.env.FILE_STORAGE_ENDPOINT ||
    (accountId ? `https://${accountId}.r2.cloudflarestorage.com` : '');
  const configuredBackend =
    process.env.FILE_STORAGE_BACKEND?.trim().toLowerCase();
  const backend: ManagedStorageBackend =
    configuredBackend === 'r2' ||
    (!configuredBackend && accessKeyId && secretAccessKey && bucket && endpoint)
      ? 'r2'
      : 'local';

  if (
    backend === 'r2' &&
    (!accessKeyId || !secretAccessKey || !bucket || !endpoint)
  ) {
    throw new Error('R2 文件存储配置不完整');
  }

  return {
    backend,
    bucket,
    endpoint: endpoint.replace(/\/$/, ''),
    accessKeyId,
    secretAccessKey,
    region: process.env.FILE_STORAGE_REGION || 'auto',
    uploadTtlSeconds: positiveInteger(
      process.env.FILE_UPLOAD_URL_TTL_SECONDS,
      300
    ),
    downloadTtlSeconds: positiveInteger(
      process.env.FILE_DOWNLOAD_URL_TTL_SECONDS,
      300
    ),
  };
}

function encodeObjectKey(key: string) {
  return key.split('/').map(encodeURIComponent).join('/');
}

function r2ObjectUrl(config: ManagedStorageConfig, objectKey: string) {
  return `${config.endpoint}/${encodeURIComponent(config.bucket)}/${encodeObjectKey(objectKey)}`;
}

function r2Client(config: ManagedStorageConfig) {
  return new AwsClient({
    accessKeyId: config.accessKeyId,
    secretAccessKey: config.secretAccessKey,
    service: 's3',
    region: config.region,
    retries: 2,
  });
}

export async function signedObjectUrl(options: {
  config?: ManagedStorageConfig;
  objectKey: string;
  method: 'GET' | 'PUT';
  expiresSeconds?: number;
  downloadFilename?: string;
  disposition?: 'attachment' | 'inline';
}) {
  const config = options.config || managedStorageConfig();
  if (config.backend !== 'r2') {
    throw new Error('本地文件存储不支持 R2 预签名 URL');
  }
  const url = new URL(r2ObjectUrl(config, options.objectKey));
  url.searchParams.set(
    'X-Amz-Expires',
    String(
      options.expiresSeconds ||
        (options.method === 'PUT'
          ? config.uploadTtlSeconds
          : config.downloadTtlSeconds)
    )
  );
  if (options.downloadFilename && options.method === 'GET') {
    const safeName = options.downloadFilename.replace(/[\r\n"\\]/g, '_');
    url.searchParams.set(
      'response-content-disposition',
      `${options.disposition || 'attachment'}; filename*=UTF-8''${encodeURIComponent(safeName)}`
    );
  }
  const request = await r2Client(config).sign(url.toString(), {
    method: options.method,
    aws: { signQuery: true },
  });
  return request.url;
}

export async function headManagedObject(objectKey: string) {
  const config = managedStorageConfig();
  if (config.backend === 'local') {
    const info = await stat(localManagedFilePath(objectKey));
    return { sizeBytes: info.size, etag: '' };
  }
  const response = await r2Client(config).fetch(
    r2ObjectUrl(config, objectKey),
    { method: 'HEAD' }
  );
  if (!response.ok) {
    throw new Error(`对象存储校验失败（${response.status}）`);
  }
  const sizeBytes = Number(response.headers.get('content-length') || '-1');
  if (!Number.isFinite(sizeBytes) || sizeBytes < 0) {
    throw new Error('对象存储没有返回有效的文件大小');
  }
  return {
    sizeBytes,
    etag: (response.headers.get('etag') || '').replace(/^"|"$/g, ''),
  };
}

export async function deleteManagedObject(objectKey: string) {
  const config = managedStorageConfig();
  if (config.backend === 'local') {
    await unlink(localManagedFilePath(objectKey)).catch(
      (error: NodeJS.ErrnoException) => {
        if (error.code !== 'ENOENT') throw error;
      }
    );
    return;
  }
  const response = await r2Client(config).fetch(
    r2ObjectUrl(config, objectKey),
    { method: 'DELETE' }
  );
  if (!response.ok && response.status !== 404) {
    throw new Error(`对象存储删除失败（${response.status}）`);
  }
}

export function localManagedStorageRoot() {
  return path.join(
    process.cwd(),
    'services',
    'cra_api',
    'runtime',
    'managed-files'
  );
}

export function localManagedFilePath(objectKey: string) {
  if (!/^[a-zA-Z0-9/_-]+$/.test(objectKey) || objectKey.includes('..')) {
    throw new Error('非法对象键');
  }
  const root = localManagedStorageRoot();
  const resolved = path.resolve(root, objectKey);
  if (!resolved.startsWith(`${path.resolve(root)}${path.sep}`)) {
    throw new Error('非法对象路径');
  }
  return resolved;
}

export async function ensureLocalManagedFileDirectory(objectKey: string) {
  const filePath = localManagedFilePath(objectKey);
  await mkdir(path.dirname(filePath), { recursive: true });
  return filePath;
}
