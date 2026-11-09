import { readFile } from 'node:fs/promises';
import pathModule from 'node:path';
import { NextRequest } from 'next/server';
import { sql } from 'drizzle-orm';

import { db } from '@/core/db';
import { isMaintenanceAdministrator } from '@/core/rbac/permission';
import { getSignUser } from '@/shared/models/user';
import {
  bindManagedFile,
  currentBoundFile,
  deleteRetiredManagedFile,
  findManagedFile,
  managedFileBrowserUrl,
  managedFileReadLocation,
  markManagedFileProcessing,
  markManagedFileReady,
  markManagedFileRejected,
  retireBoundFile,
  type ManagedFileRecord,
} from '@/shared/services/managed-files';
import { getUserRoles, hasPermission } from '@/shared/services/rbac';

type RouteContext = { params: Promise<{ path: string[] }> };

const CASE_STAGES = [
  'intake_excel',
  'resolve_entity',
  'financial_evidence',
  'financial_signoff',
  'retrieve_context',
  'risk_signoff',
  'external_score',
  'write_workbook',
  'final_signoff',
] as const;

type CaseSummaryRow = {
  case_id: string;
  title: string;
  bp: string;
  requested_cl_usd: number | string;
  customer: Record<string, unknown> | null;
  status: string;
  current_stage: string;
  stage_index: number;
  current_task: string;
  blocker: string;
  completed_tasks: number;
  total_tasks: number;
  workflow_updated_at: Date | string;
  mock: boolean;
  access: Record<string, unknown> | null;
};

type CaseDetailRow = {
  case_data: Record<string, unknown>;
  workflow: Record<string, unknown>;
  tasks: Array<Record<string, unknown>>;
  notes: Record<string, unknown>;
  events: Array<Record<string, unknown>>;
  bad_cases: Array<Record<string, unknown>>;
  stage_snapshots: Array<Record<string, unknown>>;
};

const REVIEW_ROLES = new Set([
  'senior_credit_analyst',
  'credit_manager',
  'quality_manager',
]);

function canReadCase(
  mock: boolean,
  access: Record<string, unknown> | null | undefined,
  userId: string,
  role: string
) {
  if (role === 'admin' || role === 'super_admin' || mock) return true;
  const owner = String(access?.owner_user_id || '');
  const participants = Array.isArray(access?.participants)
    ? access.participants.map(String)
    : [];
  return (
    owner === userId || participants.includes(userId) || REVIEW_ROLES.has(role)
  );
}

async function readCaseSummaries(userId: string, role: string) {
  const result = await db().execute(sql`
    SELECT
      c.case_id, c.title, c.bp, c.requested_cl_usd, c.customer,
      c.mock, COALESCE(n.payload->'case_access', '{}'::jsonb) AS access,
      w.status, w.current_stage, w.stage_index, w.current_task,
      w.blocker, w.completed_tasks, w.total_tasks,
      w.updated_at AS workflow_updated_at
    FROM cra_case c
    JOIN cra_workflow_state w ON w.case_id = c.case_id
    LEFT JOIN cra_case_note n ON n.case_id = c.case_id
    ORDER BY c.case_id
  `);
  const rows = (
    Array.isArray(result) ? result : result.rows || []
  ) as CaseSummaryRow[];
  return rows
    .filter((row) => canReadCase(row.mock, row.access, userId, role))
    .map((row) => ({
      case_id: row.case_id,
      title: row.title,
      bp: row.bp,
      requested_cl_usd: Number(row.requested_cl_usd || 0),
      customer: row.customer || {},
      workflow: {
        case_id: row.case_id,
        status: row.status,
        current_stage: row.current_stage,
        stage_index: row.stage_index,
        current_task: row.current_task,
        blocker: row.blocker,
        completed_tasks: row.completed_tasks,
        total_tasks: row.total_tasks,
        updated_at: row.workflow_updated_at,
        total_stages: CASE_STAGES.length,
        stages: CASE_STAGES,
      },
    }));
}

async function readCaseDetail(caseId: string) {
  const result = await db().execute(sql`
    SELECT
      to_jsonb(c) AS case_data,
      to_jsonb(w) AS workflow,
      COALESCE((
        SELECT jsonb_agg(to_jsonb(t) ORDER BY
          CASE t.status WHEN 'open' THEN 0 ELSE 1 END, t.id DESC)
        FROM cra_human_task t WHERE t.case_id = c.case_id
      ), '[]'::jsonb) AS tasks,
      COALESCE((
        SELECT n.payload FROM cra_case_note n WHERE n.case_id = c.case_id
      ), '{}'::jsonb) AS notes,
      COALESCE((
        SELECT jsonb_agg(to_jsonb(recent) ORDER BY recent.id DESC)
        FROM (
          SELECT * FROM cra_agent_event e
          WHERE e.case_id = c.case_id
          ORDER BY e.id DESC LIMIT 20
        ) recent
      ), '[]'::jsonb) AS events,
      COALESCE((
        SELECT jsonb_agg(to_jsonb(b) ORDER BY
          CASE b.status WHEN 'open' THEN 0 ELSE 1 END, b.id DESC)
        FROM cra_bad_case b WHERE b.case_id = c.case_id
      ), '[]'::jsonb) AS bad_cases,
      COALESCE((
        SELECT jsonb_agg(to_jsonb(s) ORDER BY s.stage_index, s.id)
        FROM cra_stage_snapshot s WHERE s.case_id = c.case_id
      ), '[]'::jsonb) AS stage_snapshots
    FROM cra_case c
    JOIN cra_workflow_state w ON w.case_id = c.case_id
    WHERE c.case_id = ${caseId}
    LIMIT 1
  `);
  const rows = (
    Array.isArray(result) ? result : result.rows || []
  ) as CaseDetailRow[];
  const row = rows[0];
  if (!row) return null;

  const currentStage = String(row.workflow.current_stage || '');
  const status = String(row.workflow.status || '');
  const expectedTask: Record<string, string> = {
    resolve_entity: 'entity_confirmation',
    financial_signoff: 'financial_signoff',
    risk_signoff: 'risk_signoff',
    final_signoff: 'final_signoff',
  };
  let humanTasks = row.tasks.filter((task) => task.status === 'open');
  if (expectedTask[currentStage]) {
    humanTasks = humanTasks.filter(
      (task) => task.task_type === expectedTask[currentStage]
    );
  } else if (status !== 'awaiting_human') {
    humanTasks = [];
  }

  return {
    ...row.case_data,
    workflow: {
      ...row.workflow,
      total_stages: CASE_STAGES.length,
      stages: CASE_STAGES,
    },
    human_tasks: humanTasks,
    events: row.events,
    notes: row.notes,
    bad_cases: row.bad_cases,
    stage_history: row.stage_snapshots,
  };
}

async function readWorkflowStage(caseId: string) {
  const result = await db().execute(sql`
    SELECT current_stage
    FROM cra_workflow_state
    WHERE case_id = ${caseId}
    LIMIT 1
  `);
  const rows = (Array.isArray(result) ? result : result.rows || []) as Array<{
    current_stage: string;
  }>;
  return rows[0]?.current_stage || '';
}

async function readFinancialReport(caseId: string) {
  const result = await db().execute(sql`
    SELECT payload->'financial_source' AS source
    FROM cra_case_note
    WHERE case_id = ${caseId}
    LIMIT 1
  `);
  const rows = (Array.isArray(result) ? result : result.rows || []) as Array<{
    source: Record<string, unknown> | null;
  }>;
  const source = rows[0]?.source;
  const storedFilename = String(source?.stored_filename || '');
  if (
    !storedFilename ||
    pathModule.basename(storedFilename) !== storedFilename
  ) {
    return null;
  }
  const filePath = pathModule.join(
    process.cwd(),
    'services/cra_api/runtime/uploads/financial-reports',
    storedFilename
  );
  const contents = await readFile(filePath);
  const displayName = String(source?.filename || `${caseId}.pdf`).replace(
    /[\r\n"]/g,
    '_'
  );
  return new Response(Uint8Array.from(contents).buffer, {
    headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': `inline; filename="${displayName}"`,
      'Cache-Control': 'private, no-store',
    },
  });
}

async function managedSourceRedirect(
  file: ManagedFileRecord | null,
  disposition: 'attachment' | 'inline' = 'attachment'
) {
  if (!file || file.storage_provider !== 'r2') return null;
  const url = await managedFileBrowserUrl(file, disposition);
  return url ? Response.redirect(url, 307) : null;
}

async function addManagedFileHeaders(
  headers: Headers,
  file: ManagedFileRecord,
  prefix = 'X-CRA-File'
) {
  const location = await managedFileReadLocation(file);
  headers.set(`${prefix}-Id`, file.id);
  headers.set(`${prefix}-Name`, encodeURIComponent(file.original_filename));
  headers.set(`${prefix}-Size`, String(file.size_bytes));
  headers.set(`${prefix}-Sha256`, file.sha256 || '');
  headers.set(`${prefix}-Mime`, file.mime_declared);
  if (location.url) headers.set(`${prefix}-Url`, location.url);
  if (location.localPath) headers.set(`${prefix}-Path`, location.localPath);
}

function expectedPurpose(path: string[]) {
  if (path[0] === 'knowledge' && path[1] === 'documents') {
    return 'knowledge_source';
  }
  if (path[0] === 'cases' && path[2] === 'financial-report') {
    return 'financial_report';
  }
  if (path[0] === 'cases' && (path.length === 1 || path[1] === 'preview')) {
    return 'case_excel';
  }
  return '';
}

async function requiredPermission(method: string, path: string[]) {
  if (path[0] === 'knowledge') {
    if (method === 'GET' || method === 'HEAD') return 'cra.knowledge.read';
    return 'admin.access';
  }
  if (method === 'GET' || method === 'HEAD') return 'cra.cases.read';
  if (path.includes('resolve')) {
    const stage =
      path[0] === 'cases' && path[1] ? await readWorkflowStage(path[1]) : '';
    return (
      (
        {
          resolve_entity: 'cra.cases.entity.confirm',
          financial_signoff: 'cra.cases.financial.signoff',
          risk_signoff: 'cra.cases.risk.signoff',
          final_signoff: 'cra.cases.final.approve',
        } as Record<string, string>
      )[stage] || 'cra.cases.final.approve'
    );
  }
  return 'cra.cases.write';
}

async function caseCapabilities(userId: string, maintenanceOnly = false) {
  if (maintenanceOnly) {
    return {
      can_write: false,
      can_confirm_entity: false,
      can_sign_financial: false,
      can_sign_risk: false,
      can_approve_final: false,
      maintenance_read_only: true,
    };
  }
  const [
    canWrite,
    canConfirmEntity,
    canSignFinancial,
    canSignRisk,
    canApproveFinal,
  ] = await Promise.all([
    hasPermission(userId, 'cra.cases.write'),
    hasPermission(userId, 'cra.cases.entity.confirm'),
    hasPermission(userId, 'cra.cases.financial.signoff'),
    hasPermission(userId, 'cra.cases.risk.signoff'),
    hasPermission(userId, 'cra.cases.final.approve'),
  ]);
  return {
    can_write: canWrite,
    can_confirm_entity: canConfirmEntity,
    can_sign_financial: canSignFinancial,
    can_sign_risk: canSignRisk,
    can_approve_final: canApproveFinal,
    maintenance_read_only: false,
  };
}

async function forward(request: NextRequest, context: RouteContext) {
  const user = await getSignUser();
  if (!user) {
    return Response.json(
      { detail: '请先登录后再访问案件数据' },
      { status: 401 }
    );
  }

  const { path } = await context.params;
  const roles = await getUserRoles(user.id);
  const maintenanceOnly = isMaintenanceAdministrator(roles);
  const isReadRequest = request.method === 'GET' || request.method === 'HEAD';
  const isMaintenanceCaseRead =
    maintenanceOnly && isReadRequest && path[0] === 'cases';
  if (maintenanceOnly && path[0] !== 'knowledge' && !isMaintenanceCaseRead) {
    return Response.json(
      {
        detail:
          '系统管理员可只读查看全部案件并负责知识库维护，但不能创建、编辑、运行或签署案件。',
      },
      { status: 403 }
    );
  }
  const permission = await requiredPermission(request.method, path);
  if (!isMaintenanceCaseRead && !(await hasPermission(user.id, permission))) {
    return Response.json(
      { detail: `当前账号缺少权限：${permission}` },
      { status: 403 }
    );
  }

  const sharedSecret = process.env.CRA_API_SHARED_SECRET;
  if (!sharedSecret) {
    return Response.json(
      { detail: 'CRA_API_SHARED_SECRET 尚未配置' },
      { status: 503 }
    );
  }

  const roleNames = roles.map((role) => role.name);
  const forwardedRole = roleNames.includes('super_admin')
    ? 'super_admin'
    : roleNames.includes('admin')
      ? 'admin'
      : roleNames.includes('credit_manager')
        ? 'credit_manager'
        : roleNames.includes('senior_credit_analyst')
          ? 'senior_credit_analyst'
          : roleNames.includes('quality_manager')
            ? 'quality_manager'
            : 'credit_analyst';

  // Read dashboard projections through the already-open Next.js database pool
  // instead of making a second network hop through FastAPI.
  if (request.method === 'GET' && path.length === 1 && path[0] === 'cases') {
    try {
      return Response.json(await readCaseSummaries(user.id, forwardedRole));
    } catch {
      return Response.json(
        { detail: '案件数据库读取失败，请稍后重试' },
        { status: 503 }
      );
    }
  }

  if (request.method === 'GET' && path.length === 2 && path[0] === 'cases') {
    try {
      const caseDetail = await readCaseDetail(path[1]);
      const allowed =
        caseDetail &&
        canReadCase(
          Boolean((caseDetail as Record<string, unknown>).mock),
          (caseDetail.notes.case_access || null) as Record<
            string,
            unknown
          > | null,
          user.id,
          forwardedRole
        );
      if (caseDetail && !allowed) {
        return Response.json(
          { detail: '当前用户不在该案件的授权范围内' },
          { status: 403 }
        );
      }
      return caseDetail
        ? Response.json({
            ...caseDetail,
            capabilities: await caseCapabilities(user.id, maintenanceOnly),
          })
        : Response.json({ detail: '案件不存在' }, { status: 404 });
    } catch {
      return Response.json(
        { detail: '案件详情读取失败，请稍后重试' },
        { status: 503 }
      );
    }
  }

  if (
    request.method === 'GET' &&
    path.length === 4 &&
    path[0] === 'cases' &&
    path[2] === 'financial-report' &&
    path[3] === 'source'
  ) {
    try {
      const caseDetail = await readCaseDetail(path[1]);
      if (
        !caseDetail ||
        !canReadCase(
          Boolean((caseDetail as Record<string, unknown>).mock),
          (caseDetail.notes.case_access || null) as Record<
            string,
            unknown
          > | null,
          user.id,
          forwardedRole
        )
      ) {
        return Response.json(
          {
            detail: caseDetail
              ? '当前用户不在该案件的授权范围内'
              : '案件不存在',
          },
          { status: caseDetail ? 403 : 404 }
        );
      }
      const managed = await currentBoundFile(
        'case',
        path[1],
        'financial_report'
      );
      const redirect = await managedSourceRedirect(managed, 'inline');
      if (redirect) return redirect;
      return (
        (await readFinancialReport(path[1])) ||
        Response.json(
          { detail: '当前案件没有可查看的正式财报原文' },
          { status: 404 }
        )
      );
    } catch {
      return Response.json({ detail: '财报原文文件不存在' }, { status: 404 });
    }
  }

  if (
    request.method === 'GET' &&
    path.length === 3 &&
    path[0] === 'cases' &&
    path[2] === 'source'
  ) {
    const caseDetail = await readCaseDetail(path[1]);
    if (
      !caseDetail ||
      !canReadCase(
        Boolean((caseDetail as Record<string, unknown>).mock),
        (caseDetail.notes.case_access || null) as Record<
          string,
          unknown
        > | null,
        user.id,
        forwardedRole
      )
    ) {
      return Response.json(
        {
          detail: caseDetail ? '当前用户不在该案件的授权范围内' : '案件不存在',
        },
        { status: caseDetail ? 403 : 404 }
      );
    }
    const redirect = await managedSourceRedirect(
      await currentBoundFile('case', path[1], 'case_excel')
    );
    if (redirect) return redirect;
  }

  if (
    request.method === 'GET' &&
    path.length === 4 &&
    path[0] === 'knowledge' &&
    path[1] === 'documents' &&
    path[3] === 'source'
  ) {
    const redirect = await managedSourceRedirect(
      await currentBoundFile('knowledge_document', path[2], 'knowledge_source'),
      'inline'
    );
    if (redirect) return redirect;
  }

  const baseUrl = process.env.CRA_API_INTERNAL_URL || 'http://127.0.0.1:8001';
  const upstreamUrl = new URL(
    path.map(encodeURIComponent).join('/'),
    `${baseUrl.replace(/\/$/, '')}/`
  );
  upstreamUrl.search = request.nextUrl.search;

  const headers = new Headers({
    Accept: request.headers.get('accept') || '*/*',
    'X-CRA-Service-Secret': sharedSecret,
    'X-CRA-User': user.id,
    'X-CRA-Name': encodeURIComponent(user.name || user.email),
    'X-CRA-Role': forwardedRole,
  });
  const contentType = request.headers.get('content-type');
  if (contentType) headers.set('Content-Type', contentType);

  let requestBody: ArrayBuffer | undefined;
  let jsonBody: Record<string, unknown> = {};
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    requestBody = await request.arrayBuffer();
    if (contentType?.includes('application/json') && requestBody.byteLength) {
      try {
        jsonBody = JSON.parse(new TextDecoder().decode(requestBody)) as Record<
          string,
          unknown
        >;
      } catch {
        return Response.json(
          { detail: 'JSON 请求体格式无效' },
          { status: 400 }
        );
      }
    }
  }

  let managedUpload: ManagedFileRecord | null = null;
  const managedFileId = String(jsonBody.file_id || '');
  if (managedFileId) {
    managedUpload = await findManagedFile(managedFileId, { ownerId: user.id });
    const purpose = expectedPurpose(path);
    if (!managedUpload || (purpose && managedUpload.purpose !== purpose)) {
      return Response.json(
        { detail: '上传文件不存在或用途不匹配' },
        { status: 404 }
      );
    }
    if (!['RECEIVED', 'READY', 'REJECTED'].includes(managedUpload.status)) {
      return Response.json(
        { detail: `文件当前状态不可处理：${managedUpload.status}` },
        { status: 409 }
      );
    }
    await addManagedFileHeaders(headers, managedUpload);
    if (jsonBody.duplicate_mode) {
      headers.set('X-CRA-Duplicate-Mode', String(jsonBody.duplicate_mode));
    }
    if (jsonBody.library_id) {
      headers.set('X-CRA-Library-Id', String(jsonBody.library_id));
    }
    if (jsonBody.title) {
      headers.set(
        'X-CRA-Document-Title',
        encodeURIComponent(String(jsonBody.title))
      );
    }
    await markManagedFileProcessing(managedUpload.id);
  }

  if (path[0] === 'cases' && path[1] && path[1] !== 'preview') {
    const caseSource = await currentBoundFile('case', path[1], 'case_excel');
    if (caseSource)
      await addManagedFileHeaders(headers, caseSource, 'X-CRA-Case-Source');
  }
  if (
    path[0] === 'knowledge' &&
    path[1] === 'documents' &&
    path[2] &&
    path[3] === 'preview'
  ) {
    const knowledgeSource = await currentBoundFile(
      'knowledge_document',
      path[2],
      'knowledge_source'
    );
    if (knowledgeSource) {
      await addManagedFileHeaders(
        headers,
        knowledgeSource,
        'X-CRA-Knowledge-Source'
      );
    }
  }

  try {
    const timeoutMs =
      request.method === 'GET' || request.method === 'HEAD' ? 15_000 : 120_000;
    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body:
        request.method === 'GET' || request.method === 'HEAD'
          ? undefined
          : requestBody,
      cache: 'no-store',
      signal: AbortSignal.timeout(timeoutMs),
    });
    const responseHeaders = new Headers();
    for (const name of [
      'content-type',
      'content-disposition',
      'content-length',
      'etag',
    ]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    if (managedUpload) {
      const payload = await upstream.arrayBuffer();
      let responseJson: Record<string, unknown> | null = null;
      if (upstream.headers.get('content-type')?.includes('application/json')) {
        try {
          responseJson = JSON.parse(new TextDecoder().decode(payload));
        } catch {
          responseJson = null;
        }
      }
      if (upstream.ok) {
        if (path[0] === 'cases' && path.length === 1 && responseJson?.case_id) {
          await bindManagedFile({
            fileId: managedUpload.id,
            entityType: 'case',
            entityId: String(responseJson.case_id),
            role: 'case_excel',
            actorUserId: user.id,
          });
        } else if (
          path[0] === 'cases' &&
          path[2] === 'financial-report' &&
          path[1]
        ) {
          await bindManagedFile({
            fileId: managedUpload.id,
            entityType: 'case',
            entityId: path[1],
            role: 'financial_report',
            actorUserId: user.id,
          });
        } else if (
          path[0] === 'knowledge' &&
          path[1] === 'documents' &&
          path.length === 2 &&
          responseJson?.id
        ) {
          await bindManagedFile({
            fileId: managedUpload.id,
            entityType: 'knowledge_document',
            entityId: String(responseJson.id),
            role: 'knowledge_source',
            actorUserId: user.id,
          });
        } else {
          await markManagedFileReady(managedUpload.id);
        }
      } else {
        const detail = responseJson?.detail;
        await markManagedFileRejected(
          managedUpload.id,
          typeof detail === 'string' ? detail : `upstream_${upstream.status}`
        );
      }
      return new Response(payload, {
        status: upstream.status,
        headers: responseHeaders,
      });
    }

    if (
      upstream.ok &&
      request.method === 'DELETE' &&
      path.length === 3 &&
      path[0] === 'knowledge' &&
      path[1] === 'documents'
    ) {
      const retired = await retireBoundFile({
        entityType: 'knowledge_document',
        entityId: path[2],
        role: 'knowledge_source',
        actorUserId: user.id,
      });
      await Promise.all(
        retired.map((fileId) => deleteRetiredManagedFile(fileId, user.id))
      );
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const timedOut =
      error instanceof DOMException && error.name === 'TimeoutError';
    return Response.json(
      {
        detail: timedOut
          ? 'CRA Agent API 响应超时，请检查 Neon 数据库连接'
          : 'CRA Agent API 当前不可用，请确认后端服务已启动',
      },
      { status: timedOut ? 504 : 502 }
    );
  }
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
