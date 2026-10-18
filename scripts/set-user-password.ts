/**
 * Safely set a Better Auth email/password credential for an existing user.
 *
 * The password is read from the terminal without echoing and is never logged.
 * If the stored hash already matches, the database is left unchanged.
 *
 * Usage:
 *   npm run auth:set-password -- --email=admin@example.com
 */

import { hashPassword, verifyPassword } from 'better-auth/crypto';
import { and, eq } from 'drizzle-orm';

import { closeDb, db } from '@/core/db';
import { envConfigs } from '@/config';
import { account, user } from '@/config/db/schema';
import { getUuid } from '@/shared/lib/hash';

function getEmailArgument() {
  const emailArgument = process.argv
    .slice(2)
    .find((argument) => argument.startsWith('--email='));

  return emailArgument?.slice('--email='.length).trim().toLowerCase() ?? '';
}

function readHidden(prompt: string): Promise<string> {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new Error('This command must be run in an interactive terminal.');
  }

  return new Promise((resolve, reject) => {
    const input = process.stdin;
    let value = '';

    const cleanup = () => {
      input.off('data', onData);
      input.setRawMode(false);
      input.pause();
    };

    const onData = (chunk: Buffer | string) => {
      const characters = chunk.toString();

      for (const character of characters) {
        if (character === '\u0003') {
          cleanup();
          process.stdout.write('\n');
          reject(new Error('Password update cancelled.'));
          return;
        }

        if (character === '\r' || character === '\n') {
          cleanup();
          process.stdout.write('\n');
          resolve(value);
          return;
        }

        if (character === '\u007f' || character === '\b') {
          value = value.slice(0, -1);
          continue;
        }

        if (character >= ' ') {
          value += character;
        }
      }
    };

    process.stdout.write(prompt);
    input.setEncoding('utf8');
    input.setRawMode(true);
    input.resume();
    input.on('data', onData);
  });
}

async function setUserPassword() {
  const email = getEmailArgument();

  if (!email) {
    throw new Error(
      'Missing email. Usage: npm run auth:set-password -- --email=user@example.com'
    );
  }

  if (envConfigs.database_provider !== 'postgresql') {
    throw new Error(
      'This password maintenance command currently supports PostgreSQL only.'
    );
  }

  const password = await readHidden(`New password for ${email}: `);
  const confirmation = await readHidden('Confirm new password: ');

  if (password.length < 8) {
    throw new Error('Password must contain at least 8 characters.');
  }

  if (password !== confirmation) {
    throw new Error('The passwords do not match. No changes were made.');
  }

  const database = db();
  const [targetUser] = await database
    .select({ id: user.id, email: user.email })
    .from(user)
    .where(eq(user.email, email))
    .limit(1);

  if (!targetUser) {
    throw new Error(`User not found: ${email}`);
  }

  const credentials = await database
    .select({ id: account.id, password: account.password })
    .from(account)
    .where(
      and(
        eq(account.userId, targetUser.id),
        eq(account.providerId, 'credential')
      )
    );

  if (
    credentials.length > 0 &&
    (
      await Promise.all(
        credentials.map(
          (credential: { password: string | null }) =>
            credential.password &&
            verifyPassword({ hash: credential.password, password })
        )
      )
    ).every(Boolean)
  ) {
    console.log(
      `Password already matches all ${credentials.length} credential record(s) for ${email}. No changes made.`
    );
    return;
  }

  const passwordHash = await hashPassword(password);
  const now = new Date();

  if (credentials.length > 0) {
    await database
      .update(account)
      .set({ password: passwordHash, updatedAt: now })
      .where(
        and(
          eq(account.userId, targetUser.id),
          eq(account.providerId, 'credential')
        )
      );
  } else {
    await database.insert(account).values({
      id: getUuid(),
      accountId: targetUser.id,
      providerId: 'credential',
      userId: targetUser.id,
      password: passwordHash,
      createdAt: now,
      updatedAt: now,
    });
  }

  const updatedCredentials = await database
    .select({ password: account.password })
    .from(account)
    .where(
      and(
        eq(account.userId, targetUser.id),
        eq(account.providerId, 'credential')
      )
    );
  const verified =
    updatedCredentials.length > 0 &&
    (
      await Promise.all(
        updatedCredentials.map(
          (credential: { password: string | null }) =>
            credential.password &&
            verifyPassword({ hash: credential.password, password })
        )
      )
    ).every(Boolean);

  if (!verified) {
    throw new Error(
      'Database write completed but password verification failed.'
    );
  }

  console.log(
    `Password updated and verified for ${email} (${updatedCredentials.length} credential record(s)).`
  );
}

setUserPassword()
  .catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`Password update failed: ${message}`);
    process.exitCode = 1;
  })
  .finally(async () => {
    await closeDb();
  });
