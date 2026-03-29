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

export async function rsaOaepEncryptBase64(
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
