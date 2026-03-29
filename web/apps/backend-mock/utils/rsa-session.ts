import crypto from 'node:crypto';

type RsaSession = {
  expiresAt: number;
  privateKeyPem: string;
  publicKeyPem: string;
};

const RSA_KEY_SIZE = 2048;
const DEFAULT_TTL_MS = 5 * 60 * 1000; // 5 minutes

const sessions = new Map<string, RsaSession>();

function now() {
  return Date.now();
}

function gcExpired() {
  const t = now();
  for (const [id, s] of sessions) {
    if (s.expiresAt <= t) sessions.delete(id);
  }
}

export function createRsaSession(ttlMs: number = DEFAULT_TTL_MS) {
  gcExpired();

  const id = crypto.randomUUID();
  const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', {
    modulusLength: RSA_KEY_SIZE,
    publicKeyEncoding: { format: 'pem', type: 'spki' },
    privateKeyEncoding: { format: 'pem', type: 'pkcs8' },
  });

  const s: RsaSession = {
    expiresAt: now() + ttlMs,
    privateKeyPem: privateKey,
    publicKeyPem: publicKey,
  };
  sessions.set(id, s);

  return {
    expiresAt: s.expiresAt,
    keyId: id,
    publicKey: s.publicKeyPem,
  };
}

/**
 * Decrypt base64 ciphertext using a one-time RSA private key.
 * - Removes the session after successful decrypt.
 * - Returns null when keyId not found/expired/decrypt failed.
 */
export function decryptOnceRsaOaepSha256(
  keyId: string,
  encryptedBase64: string,
): null | string {
  gcExpired();
  const s = sessions.get(keyId);
  if (!s) return null;
  if (s.expiresAt <= now()) {
    sessions.delete(keyId);
    return null;
  }

  try {
    const buf = Buffer.from(encryptedBase64, 'base64');
    const plain = crypto.privateDecrypt(
      {
        key: s.privateKeyPem,
        oaepHash: 'sha256',
        padding: crypto.constants.RSA_PKCS1_OAEP_PADDING,
      },
      buf,
    );
    sessions.delete(keyId);
    return plain.toString('utf8');
  } catch {
    return null;
  }
}
