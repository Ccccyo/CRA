import { readFile } from 'node:fs/promises';
import pathModule from 'node:path';
import { NextRequest } from 'next/server';
import { sql } from 'drizzle-orm';

import { db } from '@/core/db';
import { getSignUser } from '@/shared/models/user';
import { getUserRoles, hasPermission } from '@/shared/services/rbac';

type RouteContext = { params: Promise<{ path: string[] }> };

const CASE_STAGES = [
  'intake_excel',
  'resolve_entity',
  'financial_evidence',
  'financial_signoff',
  'retrieve_context',
  'risk_signoff',
  'write_workbook',
  'external_score',
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

async function readCaseSummaries() {
  const result = await db().execute(sql`
    SELECT
      c.case_id, c.title, c.bp, c.requested_cl_usd, c.customer,
      w.status, w.current_stage, w.stage_index, w.current_task,
      w.blocker, w.completed_tasks, w.total_tasks,
      w.updated_at AS workflow_updated_at
    FROM cra_case c
    JOIN cra_workflow_state w ON w.case_id = c.case_id
    ORDER BY c.case_id
  `);
  const rows = (
    Array.isArray(result) ? result : result.rows || []
  ) as CaseSummaryRow[];
  return rows.map((row) => ({
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

async function resolveFinancialSignoff(
  caseId: string,
  requestedTaskId: number,
  resolution: Record<string, unknown>
) {
  const database = db();
  await database.transaction(async (tx: ReturnType<typeof db>) => {
    const contextResult = await tx.execute(sql`
      SELECT to_jsonb(w) AS workflow,
             COALESCE(n.payload, '{}'::jsonb) AS notes
      FROM cra_workflow_state w
      LEFT JOIN cra_case_note n ON n.case_id = w.case_id
      WHERE w.case_id = ${caseId}
      FOR UPDATE OF w
    `);
    const contexts = (
      Array.isArray(contextResult) ? contextResult : contextResult.rows || []
    ) as Array<{
      workflow: Record<string, unknown>;
      notes: Record<string, unknown>;
    }>;
    const context = contexts[0];
    if (!context) throw new Error('CASE_NOT_FOUND');

    // A repeated click after success is safe and simply returns the new state.
    if (context.workflow.current_stage !== 'financial_signoff') return;

    const taskResult = await tx.execute(sql`
      SELECT id, title
      FROM cra_human_task
      WHERE case_id = ${caseId}
        AND task_type = 'financial_signoff'
        AND status = 'open'
      ORDER BY CASE WHEN id = ${requestedTaskId} THEN 0 ELSE 1 END, id DESC
      LIMIT 1
      FOR UPDATE
    `);
    const tasks = (
      Array.isArray(taskResult) ? taskResult : taskResult.rows || []
    ) as Array<{ id: number; title: string }>;
    let task = tasks[0];
    if (!task) {
      const inserted = await tx.execute(sql`
        INSERT INTO cra_human_task
          (case_id, task_type, title, description, status, payload, resolved_at)
        VALUES (
          ${caseId}, 'financial_signoff', '签署财务证据',
          '由签署动作补全的审计记录', 'resolved', '{}'::jsonb, NOW()
        )
        RETURNING id, title
      `);
      task = (Array.isArray(inserted) ? inserted[0] : inserted.rows?.[0]) as {
        id: number;
        title: string;
      };
    } else {
      await tx.execute(sql`
        UPDATE cra_human_task
        SET status = 'resolved', resolved_at = NOW()
        WHERE id = ${task.id}
      `);
    }

    const notes = { ...context.notes };
    const resolutions = {
      ...((notes.human_resolutions as Record<string, unknown>) || {}),
      financial_signoff: {
        ...resolution,
        resolved_at: new Date().toISOString(),
      },
    };
    notes.human_resolutions = resolutions;
    const notesJson = JSON.stringify(notes);
    const snapshotJson = JSON.stringify({
      workflow: context.workflow,
      notes,
    });
    await tx.execute(sql`
      INSERT INTO cra_stage_snapshot
        (case_id, stage, stage_index, title, payload)
      VALUES (
        ${caseId}, 'financial_signoff',
        ${Number(context.workflow.stage_index || 3)},
        ${String(context.workflow.current_task || '等待财务证据签署')},
        ${snapshotJson}::jsonb
      )
    `);
    await tx.execute(sql`
      INSERT INTO cra_case_note (case_id, payload)
      VALUES (${caseId}, ${notesJson}::jsonb)
      ON CONFLICT(case_id) DO UPDATE SET
        payload = EXCLUDED.payload, updated_at = NOW()
    `);
    await tx.execute(sql`
      UPDATE cra_workflow_state
      SET status = 'running', current_stage = 'retrieve_context',
          stage_index = 4, current_task = '检索当前规则、行业与历史案件',
          blocker = '', updated_at = NOW()
      WHERE case_id = ${caseId}
    `);
    await tx.execute(sql`
      INSERT INTO cra_agent_event
        (case_id, event_type, title, detail, sources)
      VALUES (
        ${caseId}, 'human_resolved', ${`${task.title} 已完成`},
        '人工责任已记录，Agent 可继续下一步。', '[]'::jsonb
      )
    `);
  });
  return readCaseDetail(caseId);
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

function requiredPermission(method: string, path: string[]) {
  if (path[0] === 'knowledge') {
    if (method === 'GET' && path.length === 1) return 'cra.knowledge.read';
    return 'admin.access';
  }
  if (method === 'GET' || method === 'HEAD') return 'cra.cases.read';
  if (path.includes('resolve')) return 'cra.cases.approve';
  return 'cra.cases.write';
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
  const permission = requiredPermission(request.method, path);
  if (!(await hasPermission(user.id, permission))) {
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

  const roles = await getUserRoles(user.id);
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
      return Response.json(await readCaseSummaries());
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
      return caseDetail
        ? Response.json(caseDetail)
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
    request.method === 'POST' &&
    path.length === 5 &&
    path[0] === 'cases' &&
    path[2] === 'tasks' &&
    path[4] === 'resolve' &&
    (await readWorkflowStage(path[1])) === 'financial_signoff'
  ) {
    try {
      const body = (await request.json().catch(() => ({}))) as {
        resolution?: Record<string, unknown>;
      };
      const result = await resolveFinancialSignoff(
        path[1],
        Number(path[3]),
        body.resolution || {}
      );
      return result
        ? Response.json(result)
        : Response.json({ detail: '案件不存在' }, { status: 404 });
    } catch {
      return Response.json(
        { detail: '财务证据签署失败，请刷新后重试' },
        { status: 500 }
      );
    }
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

  try {
    const timeoutMs =
      request.method === 'GET' || request.method === 'HEAD' ? 15_000 : 120_000;
    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body:
        request.method === 'GET' || request.method === 'HEAD'
          ? undefined
          : await request.arrayBuffer(),
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
