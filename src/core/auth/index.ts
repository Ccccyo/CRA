import { betterAuth, BetterAuthOptions } from 'better-auth';

import { getAllConfigs } from '@/shared/models/config';

import { getAuthOptions } from './config';

const LOCAL_DEMO_ADMIN_EMAIL = 'admin@example.com';
const LOCAL_DEMO_ADMIN_PASSWORD_HASH =
  process.env.DEMO_ADMIN_PASSWORD_HASH ||
  '143296410d18ce70cdfd45816db14111:75c6461062e37e4465815c742c7bb534acdd17e10293ba6e7be863d29a8b32eb79e9e69a6c57d3954d9b423687039e030d5ffd3f197b7dfa6170a3465bfec4ba';

let localDemoAdminCredentialReady: Promise<void> | undefined;

async function ensureLocalDemoAdminCredential(
  auth: ReturnType<typeof betterAuth>
) {
  const context = await auth.$context;
  const targetUser = await context.internalAdapter.findUserByEmail(
    LOCAL_DEMO_ADMIN_EMAIL,
    { includeAccounts: true }
  );
  if (!targetUser) {
    throw new Error(`Demo administrator not found: ${LOCAL_DEMO_ADMIN_EMAIL}`);
  }

  const credentials = targetUser.accounts.filter(
    (credential) => credential.providerId === 'credential'
  );

  if (credentials.length === 0) {
    await context.internalAdapter.linkAccount({
      accountId: targetUser.user.id,
      providerId: 'credential',
      userId: targetUser.user.id,
      password: LOCAL_DEMO_ADMIN_PASSWORD_HASH,
    });
    return;
  }

  if (
    credentials.some(
      (credential) => credential.password !== LOCAL_DEMO_ADMIN_PASSWORD_HASH
    )
  ) {
    await context.internalAdapter.updatePassword(
      targetUser.user.id,
      LOCAL_DEMO_ADMIN_PASSWORD_HASH
    );
  }
}

// get auth instance in server side
export async function getAuth() {
  // get configs from db and env
  const configs = await getAllConfigs();

  const authOptions = await getAuthOptions(configs);

  const auth = betterAuth(authOptions as BetterAuthOptions);

  if (process.env.NODE_ENV !== 'production') {
    localDemoAdminCredentialReady ??= ensureLocalDemoAdminCredential(
      auth
    ).catch((error) => {
      localDemoAdminCredentialReady = undefined;
      throw error;
    });
    await localDemoAdminCredentialReady;
  }

  return auth;
}
