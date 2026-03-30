/**
 * 与后端 `cryptography` RSA-OAEP-SHA256（MGF1-SHA256、label 空）对齐的加密。
 * - 安全上下文下优先用 Web Crypto（性能更好）
 * - 纯 HTTP + 非 localhost 时无 crypto.subtle，使用 node-forge 纯 JS 实现，仍为非明文传输
 */
import forge from 'node-forge';

function pemToArrayBuffer(pem: string): ArrayBuffer {
  const b64 = pem
    .replaceAll(/-----(BEGIN|END) PUBLIC KEY-----/g, '')
    .replaceAll(/\s+/g, '');
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte!);
  }
  return btoa(binary);
}

function rsaOaepEncryptForgeBase64(publicKeyPem: string, plaintext: string): string {
  const publicKey = forge.pki.publicKeyFromPem(publicKeyPem);
  const encoded = forge.util.encodeUtf8(plaintext);
  const encrypted = publicKey.encrypt(encoded, 'RSA-OAEP', {
    md: forge.md.sha256.create(),
    mgf1: {
      md: forge.md.sha256.create(),
    },
  });
  return forge.util.encode64(encrypted);
}

async function rsaOaepEncryptSubtleBase64(
  publicKeyPem: string,
  plaintext: string,
): Promise<string> {
  const keyData = pemToArrayBuffer(publicKeyPem);
  const key = await crypto.subtle.importKey(
    'spki',
    keyData,
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    false,
    ['encrypt'],
  );
  const data = new TextEncoder().encode(plaintext);
  const encrypted = await crypto.subtle.encrypt(
    { name: 'RSA-OAEP' },
    key,
    data,
  );
  return arrayBufferToBase64(encrypted);
}

export async function rsaOaepEncryptBase64(
  publicKeyPem: string,
  plaintext: string,
): Promise<string> {
  if (typeof globalThis.crypto?.subtle !== 'undefined') {
    return rsaOaepEncryptSubtleBase64(publicKeyPem, plaintext);
  }
  return rsaOaepEncryptForgeBase64(publicKeyPem, plaintext);
}
