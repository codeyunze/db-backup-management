import { defineEventHandler, readBody, setResponseStatus } from 'h3';
import {
  clearRefreshTokenCookie,
  setRefreshTokenCookie,
} from '~/utils/cookie-utils';
import { generateAccessToken, generateRefreshToken } from '~/utils/jwt-utils';
import { MOCK_USERS } from '~/utils/mock-data';
import {
  forbiddenResponse,
  useResponseError,
  useResponseSuccess,
} from '~/utils/response';
import { decryptOnceRsaOaepSha256 } from '~/utils/rsa-session';

export default defineEventHandler(async (event) => {
  const { encryptedPassword, keyId, password, username } =
    await readBody(event);
  const resolvedPassword =
    encryptedPassword && keyId
      ? decryptOnceRsaOaepSha256(String(keyId), String(encryptedPassword))
      : password;

  if (!username || (!password && !(encryptedPassword && keyId))) {
    setResponseStatus(event, 400);
    return useResponseError(
      'BadRequestException',
      'Username and password are required',
    );
  }

  if (encryptedPassword && keyId && !resolvedPassword) {
    setResponseStatus(event, 400);
    return useResponseError(
      'BadRequestException',
      'Invalid RSA key or payload',
    );
  }

  const findUser = MOCK_USERS.find(
    (item) => item.username === username && item.password === resolvedPassword,
  );

  if (!findUser) {
    clearRefreshTokenCookie(event);
    return forbiddenResponse(event, 'Username or password is incorrect.');
  }

  const accessToken = generateAccessToken(findUser);
  const refreshToken = generateRefreshToken(findUser);

  setRefreshTokenCookie(event, refreshToken);

  return useResponseSuccess({
    ...findUser,
    accessToken,
  });
});
