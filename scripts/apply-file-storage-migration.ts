import { readFile } from 'node:fs/promises';
import path from 'node:path';
import postgres from 'postgres';

async function main() {
  const databaseUrl = process.env.DATABASE_URL?.trim();
  if (!databaseUrl) {
    throw new Error('DATABASE_URL is required');
  }

  const migrationPath = path.join(
    process.cwd(),
    'src/config/db/migrations/0003_enterprise_file_storage.sql'
  );
  const source = await readFile(migrationPath, 'utf8');
  const statements = source
    .split('--> statement-breakpoint')
    .map((statement) => statement.trim())
    .filter(Boolean);

  for (const statement of statements) {
    if (
      !/^CREATE (TABLE|UNIQUE INDEX|INDEX) IF NOT EXISTS\b/i.test(statement)
    ) {
      throw new Error(
        `Refusing non-additive migration statement: ${statement.slice(0, 80)}`
      );
    }
  }

  const client = postgres(databaseUrl, { max: 1, prepare: false });
  try {
    await client.begin(async (transaction) => {
      for (const statement of statements) {
        await transaction.unsafe(statement);
      }
      await transaction`
        INSERT INTO cra_data_migration (id, completed_at)
        VALUES ('enterprise-file-storage-v1', NOW())
        ON CONFLICT (id) DO UPDATE SET completed_at = EXCLUDED.completed_at
      `;
    });
    console.log(
      `Applied ${statements.length} additive file-storage statements.`
    );
  } finally {
    await client.end();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
