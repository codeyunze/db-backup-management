import { defineEventHandler } from 'h3';
import { useResponseSuccess } from '~/utils/response';
import { createRsaSession } from '~/utils/rsa-session';

export default defineEventHandler(() => {
  const { expiresAt, keyId, publicKey } = createRsaSession();
  return useResponseSuccess({
    algorithm: 'RSA-OAEP-SHA256',
    expiresAt,
    keyId,
    publicKey,
  });
});
