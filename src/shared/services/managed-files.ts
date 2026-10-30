import { sql } from 'drizzle-orm';

import { db } from '@/core/db';

import {
  deleteManagedObject,
  localManagedFilePath,
  managedStorageConfig,
  signedObjectUrl,
} from './managed-file-storage';

export type ManagedFileRecord = {
  id: string;
  purpose: string;
  storage_provider: 'local' | 'r2';
  bucket: string;
  object_key: string;
  original_filename: string;
  mime_declared: string;
  size_bytes: number;
  sha256: string;
  status: string;
  version: number;
  uploaded_by: string;
};

function rows<T>(result: unknown): T[] {
  if (Array.isArray(result)) return result as T[];
  if (result && typeof result === 'object' && 'rows' in result) {
    const nested = (result as { rows?: unknown }).rows;
    if (Array.isArray(nested)) return nested as T[];
  }
  return [];
}

export async function findManagedFile(
  fileId: string,
  options: { ownerId?: string } = {}
) {
  const result = options.ownerId
    ? await db().execute(sql`
        SELECT id, purpose, storage_provider, bucket, object_key,
               original_filename, mime_declared, size_bytes, sha256,
               status, version, uploaded_by
        FROM cra_file_object
        WHERE id = ${fileId} AND uploaded_by = ${options.ownerId}
          AND deleted_at IS NULL
        LIMIT 1
      `)
    : await db().execute(sql`
        SELECT id, purpose, storage_provider, bucket, object_key,
               original_filename, mime_declared, size_bytes, sha256,
               status, version, uploaded_by
        FROM cra_file_object
        WHERE id = ${fileId} AND deleted_at IS NULL
        LIMIT 1
      `);
  return rows<ManagedFileRecord>(result)[0] || null;
}

export async function currentBoundFile(
  entityType: string,
  entityId: string,
  role: string
) {
  const result = await db().execute(sql`
    SELECT f.id, f.purpose, f.storage_provider, f.bucket, f.object_key,
           f.original_filename, f.mime_declared, f.size_bytes, f.sha256,
           f.status, f.version, f.uploaded_by
    FROM cra_file_binding b
    JOIN cra_file_object f ON f.id = b.file_id
    WHERE b.entity_type = ${entityType} AND b.entity_id = ${entityId}
      AND b.role = ${role} AND b.is_current = true
      AND f.deleted_at IS NULL AND f.status = 'READY'
    ORDER BY b.version DESC
    LIMIT 1
  `);
  return rows<ManagedFileRecord>(result)[0] || null;
}

export async function managedFileReadLocation(file: ManagedFileRecord) {
  if (file.storage_provider === 'local') {
    return {
      url: '',
      localPath: localManagedFilePath(file.object_key),
    };
  }
  return {
    url: await signedObjectUrl({ objectKey: file.object_key, method: 'GET' }),
    localPath: '',
  };
}

export async function managedFileBrowserUrl(
  file: ManagedFileRecord,
  disposition: 'attachment' | 'inline' = 'attachment'
) {
  if (managedStorageConfig().backend !== 'r2') return '';
  return signedObjectUrl({
    objectKey: file.object_key,
    method: 'GET',
    downloadFilename: file.original_filename,
    disposition,
  });
}

export async function markManagedFileProcessing(fileId: string) {
  await db().execute(sql`
    UPDATE cra_file_object
    SET status = 'PROCESSING', updated_at = NOW()
    WHERE id = ${fileId} AND status IN ('RECEIVED', 'READY', 'REJECTED')
  `);
}

export async function markManagedFileReady(fileId: string) {
  await db().execute(sql`
    UPDATE cra_file_object
    SET status = 'READY', ready_at = COALESCE(ready_at, NOW()), updated_at = NOW()
    WHERE id = ${fileId} AND deleted_at IS NULL
  `);
}

export async function markManagedFileRejected(fileId: string, detail: string) {
  await db().execute(sql`
    UPDATE cra_file_object
    SET status = 'REJECTED', updated_at = NOW(),
        metadata = metadata || ${JSON.stringify({ rejection_reason: detail.slice(0, 500) })}::jsonb
    WHERE id = ${fileId} AND deleted_at IS NULL
  `);
}

export async function bindManagedFile(options: {
  fileId: string;
  entityType: string;
  entityId: string;
  role: string;
  actorUserId: string;
}) {
  await db().transaction(async (tx: ReturnType<typeof db>) => {
    const versionResult = await tx.execute(sql`
      SELECT COALESCE(MAX(version), 0) + 1 AS next_version
      FROM cra_file_binding
      WHERE entity_type = ${options.entityType}
        AND entity_id = ${options.entityId}
        AND role = ${options.role}
    `);
    const nextVersion = Number(
      rows<{ next_version: number | string }>(versionResult)[0]?.next_version ||
        1
    );
    await tx.execute(sql`
      UPDATE cra_file_binding
      SET is_current = false
      WHERE entity_type = ${options.entityType}
        AND entity_id = ${options.entityId}
        AND role = ${options.role}
        AND is_current = true
    `);
    await tx.execute(sql`
      INSERT INTO cra_file_binding
        (file_id, entity_type, entity_id, role, version, is_current)
      VALUES (
        ${options.fileId}, ${options.entityType}, ${options.entityId},
        ${options.role}, ${nextVersion}, true
      )
      ON CONFLICT (entity_type, entity_id, role, version) DO NOTHING
    `);
    await tx.execute(sql`
      UPDATE cra_file_object
      SET status = 'READY', ready_at = COALESCE(ready_at, NOW()), updated_at = NOW()
      WHERE id = ${options.fileId} AND deleted_at IS NULL
    `);
    await tx.execute(sql`
      INSERT INTO cra_file_audit_log
        (file_id, actor_user_id, action, result, detail)
      VALUES (
        ${options.fileId}, ${options.actorUserId}, 'bind', 'success',
        ${JSON.stringify({
          entity_type: options.entityType,
          entity_id: options.entityId,
          role: options.role,
          version: nextVersion,
        })}::jsonb
      )
    `);
  });
}

export async function retireBoundFile(options: {
  entityType: string;
  entityId: string;
  role: string;
  actorUserId: string;
}) {
  const result = await db().execute(sql`
    UPDATE cra_file_binding
    SET is_current = false
    WHERE entity_type = ${options.entityType}
      AND entity_id = ${options.entityId}
      AND role = ${options.role}
      AND is_current = true
    RETURNING file_id
  `);
  const fileIds = rows<{ file_id: string }>(result).map((row) => row.file_id);
  for (const fileId of fileIds) {
    await db().execute(sql`
      INSERT INTO cra_file_audit_log
        (file_id, actor_user_id, action, result, detail)
      VALUES (
        ${fileId}, ${options.actorUserId}, 'unbind', 'success',
        ${JSON.stringify({
          entity_type: options.entityType,
          entity_id: options.entityId,
          role: options.role,
        })}::jsonb
      )
    `);
  }
  return fileIds;
}

export async function deleteRetiredManagedFile(
  fileId: string,
  actorUserId: string
) {
  const file = await findManagedFile(fileId);
  if (!file) return;
  const currentResult = await db().execute(sql`
    SELECT 1 FROM cra_file_binding
    WHERE file_id = ${fileId} AND is_current = true
    LIMIT 1
  `);
  if (rows(currentResult).length) return;
  await deleteManagedObject(file.object_key);
  await db().transaction(async (tx: ReturnType<typeof db>) => {
    await tx.execute(sql`
      UPDATE cra_file_object
      SET status = 'DELETED', deleted_at = NOW(), updated_at = NOW()
      WHERE id = ${fileId}
    `);
    await tx.execute(sql`
      INSERT INTO cra_file_audit_log
        (file_id, actor_user_id, action, result, detail)
      VALUES (${fileId}, ${actorUserId}, 'delete', 'success', '{}'::jsonb)
    `);
  });
}
