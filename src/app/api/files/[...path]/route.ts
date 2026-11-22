import {
  createHash,
  createHmac,
  randomUUID,
  timingSafeEqual,
} from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { NextRequest } from 'next/server';
import { sql } from 'drizzle-orm';

import { db } from '@/core/db';
import { getSignUser } from '@/shared/models/user';
import {
  deleteManagedObject,
  ensureLocalManagedFileDirectory,
  headManagedObject,
  managedStorageConfig,
  signedObjectUrl,
} from '@/shared/services/managed-file-storage';
import { hasPermission } from '@/shared/services/rbac';

type RouteContext = { params: Promise<{ path: string[] }> };

type FilePurpose = 'case_excel' | 'financial_report' | 'knowledge_source';

type FileRow = {
  id: string;
  purpose: FilePurpose;
  storage_provider: 'local' | 'r2' | 'database';
  bucket: string;
  object_key: string;
  original_filename: string;
  mime_declared: string;
  size_bytes: number;
  sha256: string;
  etag: string;
  status: string;
  version: number;
  uploaded_by: string;
  created_at: string | Date;
  ready_at: string | Date | null;
};

const PURPOSES: Record<
  FilePurpose,
  { permission: string; maxBytes: number; extensions: Set<string> }
> = {
  case_excel: {
    permission: 'cra.cases.write',
    maxBytes: 20 * 1024 * 1024,
    extensions: new Set(['.xlsx']),
  },
  financial_report: {
    permission: 'cra.cases.write',
    maxBytes: 20 * 1024 * 1024,
    extensions: new Set(['.pdf']),
  },
  knowledge_source: {
    permission: 'admin.access',
    maxBytes: 10 * 1024 * 1024,
    extensions: new Set([
      '.png',
      '.jpg',
      '.jpeg',
      '.md',
      '.txt',
      '.pdf',
      '.doc',
      '.docx',
    ]),
  },
};

function rows<T>(result: unknown): T[] {
  if (Array.isArray(result)) return result as T[];
  if (result && typeof result === 'object' && 'rows' in result) {
    const nested = (result as { rows?: unknown }).rows;
    if (Array.isArray(nested)) return nested as T[];
  }
  return [];
}

function extension(filename: string) {
  const normalized = filename.trim().toLowerCase();
  const dot = normalized.lastIndexOf('.');
  return dot >= 0 ? normalized.slice(dot) : '';
}

function safeFilename(filename: string) {
  return filename.replace(/[\u0000-\u001f\u007f/\\]/g, '_').slice(0, 240);
}

async function requireUser() {
  const user = await getSignUser();
  if (!user) {
    return {
      error: Response.json({ detail: '请先登录' }, { status: 401 }),
      user: null,
    };
  }
  return { error: null, user };
}

async function ownedFile(fileId: string, userId: string) {
  const result = await db().execute(sql`
    SELECT id, purpose, storage_provider, bucket, object_key,
           original_filename, mime_declared, size_bytes, sha256, etag,
           status, version, uploaded_by, created_at, ready_at
    FROM cra_file_object
    WHERE id = ${fileId} AND uploaded_by = ${userId} AND deleted_at IS NULL
    LIMIT 1
  `);
  return rows<FileRow>(result)[0] || null;
}

async function audit(
  fileId: string,
  actorUserId: string,
  action: string,
  result: string,
  request: NextRequest,
  detail: Record<string, unknown> = {}
) {
  const requestId =
    request.headers.get('x-request-id') ||
    request.headers.get('x-vercel-id') ||
    '';
  await db().execute(sql`
    INSERT INTO cra_file_audit_log
      (file_id, actor_user_id, action, result, request_id, detail)
    VALUES (
      ${fileId}, ${actorUserId}, ${action}, ${result}, ${requestId},
      ${JSON.stringify(detail)}::jsonb
    )
  `);
}

async function initUpload(request: NextRequest, user: { id: string }) {
  const body = (await request.json().catch(() => null)) as null | {
    purpose?: FilePurpose;
    filename?: string;
    contentType?: string;
    sizeBytes?: number;
    sha256?: string;
    idempotencyKey?: string;
    replacesFileId?: string;
  };
  if (!body || !body.purpose || !PURPOSES[body.purpose]) {
    return Response.json({ detail: '文件用途无效' }, { status: 422 });
  }
  const policy = PURPOSES[body.purpose];
  if (!(await hasPermission(user.id, policy.permission))) {
    return Response.json(
      { detail: `当前账号缺少权限：${policy.permission}` },
      { status: 403 }
    );
  }
  const filename = safeFilename(String(body.filename || ''));
  const sizeBytes = Number(body.sizeBytes || 0);
  const contentType = String(
    body.contentType || 'application/octet-stream'
  ).slice(0, 160);
  const sha256 = String(body.sha256 || '').toLowerCase();
  const config = managedStorageConfig();
  const maxBytes =
    config.backend === 'database'
      ? Math.min(policy.maxBytes, 4 * 1024 * 1024)
      : policy.maxBytes;
  if (!filename || !policy.extensions.has(extension(filename))) {
    return Response.json({ detail: '文件扩展名不受支持' }, { status: 415 });
  }
  if (!Number.isSafeInteger(sizeBytes) || sizeBytes <= 0) {
    return Response.json({ detail: '文件不能为空' }, { status: 422 });
  }
  if (sizeBytes > maxBytes) {
    return Response.json(
      {
        detail:
          config.backend === 'database'
            ? '当前托管模式仅支持 4 MB 以内文件，请配置 R2 后上传更大文件'
            : '文件超过允许的大小上限',
      },
      { status: 413 }
    );
  }
  if (sha256 && !/^[a-f0-9]{64}$/.test(sha256)) {
    return Response.json({ detail: 'SHA-256 格式无效' }, { status: 422 });
  }

  const idempotencyKey =
    request.headers.get('idempotency-key') ||
    body.idempotencyKey ||
    randomUUID();
  const existingResult = await db().execute(sql`
    SELECT f.id, f.object_key, f.original_filename, f.storage_provider,
           f.status, s.expires_at
    FROM cra_upload_session s
    JOIN cra_file_object f ON f.id = s.file_id
    WHERE s.created_by = ${user.id} AND s.idempotency_key = ${idempotencyKey}
      AND f.deleted_at IS NULL
    LIMIT 1
  `);
  const existing = rows<{
    id: string;
    object_key: string;
    original_filename: string;
    storage_provider: 'local' | 'r2' | 'database';
    status: string;
    expires_at: Date | string;
  }>(existingResult)[0];
  if (existing) {
    const uploadUrl =
      existing.storage_provider === 'r2'
        ? await signedObjectUrl({
            objectKey: existing.object_key,
            method: 'PUT',
          })
        : `/api/files/${encodeURIComponent(existing.id)}/content`;
    return Response.json({
      fileId: existing.id,
      uploadUrl,
      method: 'PUT',
      headers: { 'Content-Type': contentType },
      expiresAt: existing.expires_at,
      status: existing.status,
    });
  }

  let version = 1;
  let parentFileId: string | null = null;
  if (body.replacesFileId) {
    const parent = await ownedFile(body.replacesFileId, user.id);
    if (!parent || parent.purpose !== body.purpose) {
      return Response.json({ detail: '被替换的文件不存在' }, { status: 404 });
    }
    version = parent.version + 1;
    parentFileId = parent.id;
  }

  const fileId = randomUUID();
  const sessionId = randomUUID();
  const objectKey = `cra/${body.purpose}/${new Date().toISOString().slice(0, 7).replace('-', '/')}/${fileId}`;
  const expiresAt = new Date(
    Date.now() + config.uploadTtlSeconds * 1000
  ).toISOString();
  await db().transaction(async (tx: ReturnType<typeof db>) => {
    await tx.execute(sql`
      INSERT INTO cra_file_object
        (id, purpose, storage_provider, bucket, object_key, original_filename,
         mime_declared, size_bytes, sha256, status, version, parent_file_id,
         uploaded_by, metadata)
      VALUES (
        ${fileId}, ${body.purpose}, ${config.backend}, ${config.bucket},
        ${objectKey}, ${filename}, ${contentType}, ${sizeBytes}, ${sha256},
        'INITIATED', ${version}, ${parentFileId}, ${user.id}, '{}'::jsonb
      )
    `);
    await tx.execute(sql`
      INSERT INTO cra_upload_session
        (id, file_id, idempotency_key, expected_size_bytes, expected_mime,
         expected_sha256, status, created_by, expires_at)
      VALUES (
        ${sessionId}, ${fileId}, ${idempotencyKey}, ${sizeBytes}, ${contentType},
        ${sha256}, 'OPEN', ${user.id}, ${expiresAt}
      )
    `);
  });
  await audit(fileId, user.id, 'init', 'success', request, {
    purpose: body.purpose,
    size_bytes: sizeBytes,
    storage_provider: config.backend,
  });

  const uploadUrl =
    config.backend === 'r2'
      ? await signedObjectUrl({ objectKey, method: 'PUT' })
      : `/api/files/${encodeURIComponent(fileId)}/content`;
  return Response.json(
    {
      fileId,
      uploadUrl,
      method: 'PUT',
      headers: { 'Content-Type': contentType },
      expiresAt,
      status: 'INITIATED',
    },
    { status: 201 }
  );
}

async function uploadContent(
  request: NextRequest,
  user: { id: string },
  fileId: string
) {
  const file = await ownedFile(fileId, user.id);
  if (!file) return Response.json({ detail: '文件不存在' }, { status: 404 });
  if (file.storage_provider === 'r2') {
    return Response.json(
      { detail: '生产文件必须直传对象存储' },
      { status: 404 }
    );
  }
  if (!['INITIATED', 'UPLOADING'].includes(file.status)) {
    return Response.json({ detail: '当前文件状态不能上传' }, { status: 409 });
  }
  const payload = new Uint8Array(await request.arrayBuffer());
  if (payload.byteLength !== file.size_bytes) {
    return Response.json(
      { detail: '实际文件大小与初始化记录不一致' },
      { status: 422 }
    );
  }
  const digest = createHash('sha256').update(payload).digest('hex');
  if (file.sha256 && digest !== file.sha256) {
    await audit(file.id, user.id, 'upload', 'rejected', request, {
      reason: 'sha256_mismatch',
    });
    return Response.json({ detail: '文件完整性校验失败' }, { status: 422 });
  }
  if (file.storage_provider === 'database') {
    await db().execute(sql`
      UPDATE cra_file_object
      SET content = ${Buffer.from(payload)}, etag = ${digest},
          status = 'UPLOADING', updated_at = NOW()
      WHERE id = ${file.id}
    `);
  } else {
    const filePath = await ensureLocalManagedFileDirectory(file.object_key);
    await writeFile(filePath, payload);
    await db().execute(sql`
      UPDATE cra_file_object
      SET etag = ${digest}, status = 'UPLOADING', updated_at = NOW()
      WHERE id = ${file.id}
    `);
  }
  return new Response(null, { status: 204 });
}

async function completeUpload(
  request: NextRequest,
  user: { id: string },
  fileId: string
) {
  const file = await ownedFile(fileId, user.id);
  if (!file) return Response.json({ detail: '文件不存在' }, { status: 404 });
  if (['RECEIVED', 'PROCESSING', 'READY'].includes(file.status)) {
    return Response.json({ fileId: file.id, status: file.status });
  }
  const sessionResult = await db().execute(sql`
    SELECT expires_at FROM cra_upload_session
    WHERE file_id = ${file.id} AND status = 'OPEN'
    LIMIT 1
  `);
  const session = rows<{ expires_at: Date | string }>(sessionResult)[0];
  if (!session || new Date(session.expires_at).getTime() < Date.now()) {
    return Response.json(
      { detail: '上传会话已过期，请重新上传' },
      { status: 410 }
    );
  }
  let objectInfo: { sizeBytes: number; etag: string };
  try {
    if (file.storage_provider === 'database') {
      const contentResult = await db().execute(sql`
        SELECT octet_length(content) AS size_bytes, etag
        FROM cra_file_object
        WHERE id = ${file.id} AND content IS NOT NULL
        LIMIT 1
      `);
      const stored = rows<{ size_bytes: number | string; etag: string }>(
        contentResult
      )[0];
      if (!stored) throw new Error('database_content_missing');
      objectInfo = {
        sizeBytes: Number(stored.size_bytes),
        etag: stored.etag || '',
      };
    } else {
      objectInfo = await headManagedObject(file.object_key);
    }
  } catch (error) {
    await audit(file.id, user.id, 'complete', 'failed', request, {
      reason: error instanceof Error ? error.message : 'head_failed',
    });
    return Response.json({ detail: '没有找到已上传的文件' }, { status: 422 });
  }
  if (objectInfo.sizeBytes !== file.size_bytes) {
    await audit(file.id, user.id, 'complete', 'rejected', request, {
      expected_size: file.size_bytes,
      actual_size: objectInfo.sizeBytes,
    });
    return Response.json({ detail: '对象大小校验失败' }, { status: 422 });
  }
  await db().transaction(async (tx: ReturnType<typeof db>) => {
    await tx.execute(sql`
      UPDATE cra_file_object
      SET status = 'RECEIVED', etag = ${objectInfo.etag}, updated_at = NOW()
      WHERE id = ${file.id}
    `);
    await tx.execute(sql`
      UPDATE cra_upload_session
      SET status = 'COMPLETED', completed_at = NOW()
      WHERE file_id = ${file.id} AND status = 'OPEN'
    `);
  });
  await audit(file.id, user.id, 'complete', 'success', request, {
    size_bytes: objectInfo.sizeBytes,
  });
  return Response.json({ fileId: file.id, status: 'RECEIVED' });
}

async function fileStatus(user: { id: string }, fileId: string) {
  const file = await ownedFile(fileId, user.id);
  if (!file) return Response.json({ detail: '文件不存在' }, { status: 404 });
  return Response.json({
    fileId: file.id,
    purpose: file.purpose,
    filename: file.original_filename,
    contentType: file.mime_declared,
    sizeBytes: file.size_bytes,
    sha256: file.sha256,
    status: file.status,
    version: file.version,
    createdAt: file.created_at,
    readyAt: file.ready_at,
  });
}

async function serviceContent(request: NextRequest, fileId: string) {
  const secret = process.env.CRA_API_SHARED_SECRET || '';
  const expires = Number(request.nextUrl.searchParams.get('expires') || 0);
  const disposition =
    request.nextUrl.searchParams.get('disposition') === 'inline'
      ? 'inline'
      : 'attachment';
  const signature = request.nextUrl.searchParams.get('signature') || '';
  const expected = createHmac('sha256', secret)
    .update(`${fileId}.${expires}.${disposition}`)
    .digest('hex');
  const suppliedBytes = Buffer.from(signature, 'hex');
  const expectedBytes = Buffer.from(expected, 'hex');
  const valid =
    Boolean(secret) &&
    Number.isSafeInteger(expires) &&
    expires >= Math.floor(Date.now() / 1000) &&
    suppliedBytes.length === expectedBytes.length &&
    timingSafeEqual(suppliedBytes, expectedBytes);
  if (!valid) {
    return Response.json({ detail: '文件访问签名无效或已过期' }, { status: 403 });
  }
  const result = await db().execute(sql`
    SELECT original_filename, mime_declared, content, status
    FROM cra_file_object
    WHERE id = ${fileId} AND storage_provider = 'database'
      AND deleted_at IS NULL
    LIMIT 1
  `);
  const file = rows<{
    original_filename: string;
    mime_declared: string;
    content: Buffer | Uint8Array | null;
    status: string;
  }>(result)[0];
  if (
    !file ||
    !file.content ||
    !['RECEIVED', 'PROCESSING', 'READY', 'REJECTED'].includes(file.status)
  ) {
    return Response.json({ detail: '文件内容不存在' }, { status: 404 });
  }
  const safeName = file.original_filename.replace(/[\r\n"\\]/g, '_');
  return new Response(Uint8Array.from(file.content).buffer, {
    headers: {
      'Content-Type': file.mime_declared || 'application/octet-stream',
      'Content-Length': String(file.content.byteLength),
      'Content-Disposition': `${disposition}; filename*=UTF-8''${encodeURIComponent(safeName)}`,
      'Cache-Control': 'private, no-store',
    },
  });
}

async function downloadUrl(
  request: NextRequest,
  user: { id: string },
  fileId: string
) {
  const file = await ownedFile(fileId, user.id);
  if (!file) return Response.json({ detail: '文件不存在' }, { status: 404 });
  if (file.status !== 'READY') {
    return Response.json({ detail: '文件尚未完成业务校验' }, { status: 409 });
  }
  const config = managedStorageConfig();
  if (config.backend === 'local') {
    return Response.json(
      { detail: '本地文件通过关联业务接口访问' },
      { status: 409 }
    );
  }
  const url = await signedObjectUrl({
    objectKey: file.object_key,
    method: 'GET',
    downloadFilename: file.original_filename,
  });
  await audit(file.id, user.id, 'download_url', 'success', request);
  return Response.json({ url, expiresIn: config.downloadTtlSeconds });
}

async function deleteFile(
  request: NextRequest,
  user: { id: string },
  fileId: string
) {
  const file = await ownedFile(fileId, user.id);
  if (!file) return Response.json({ detail: '文件不存在' }, { status: 404 });
  const bindingResult = await db().execute(sql`
    SELECT 1 FROM cra_file_binding
    WHERE file_id = ${file.id} AND is_current = true
    LIMIT 1
  `);
  if (rows(bindingResult).length) {
    return Response.json(
      { detail: '当前业务正在使用该文件，请先删除或替换关联文档' },
      { status: 409 }
    );
  }
  try {
    if (file.storage_provider !== 'database') {
      await deleteManagedObject(file.object_key);
    }
  } catch (error) {
    await audit(file.id, user.id, 'delete', 'failed', request, {
      reason: error instanceof Error ? error.message : 'delete_failed',
    });
    return Response.json(
      { detail: '对象存储删除失败，请稍后重试' },
      { status: 502 }
    );
  }
  await db().execute(sql`
    UPDATE cra_file_object
    SET status = 'DELETED', content = NULL,
        deleted_at = NOW(), updated_at = NOW()
    WHERE id = ${file.id}
  `);
  await audit(file.id, user.id, 'delete', 'success', request);
  return Response.json({ deleted: true, fileId: file.id });
}

async function route(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  if (
    request.method === 'GET' &&
    path.length === 2 &&
    path[1] === 'service-content'
  ) {
    try {
      return await serviceContent(request, path[0]);
    } catch (error) {
      console.error('managed service content failed', error);
      return Response.json({ detail: '文件内容读取失败' }, { status: 503 });
    }
  }
  const auth = await requireUser();
  if (auth.error || !auth.user) return auth.error;
  try {
    if (request.method === 'POST' && path.length === 1 && path[0] === 'init') {
      return await initUpload(request, auth.user);
    }
    if (path.length === 2) {
      const [fileId, action] = path;
      if (request.method === 'PUT' && action === 'content') {
        return await uploadContent(request, auth.user, fileId);
      }
      if (request.method === 'POST' && action === 'complete') {
        return await completeUpload(request, auth.user, fileId);
      }
      if (request.method === 'GET' && action === 'status') {
        return await fileStatus(auth.user, fileId);
      }
      if (request.method === 'POST' && action === 'download-url') {
        return await downloadUrl(request, auth.user, fileId);
      }
    }
    if (request.method === 'DELETE' && path.length === 1) {
      return await deleteFile(request, auth.user, path[0]);
    }
    return Response.json({ detail: '文件接口不存在' }, { status: 404 });
  } catch (error) {
    console.error('managed file API failed', error);
    return Response.json(
      { detail: '文件服务暂时不可用，请检查数据库和对象存储配置' },
      { status: 503 }
    );
  }
}

export const GET = route;
export const POST = route;
export const PUT = route;
export const DELETE = route;
