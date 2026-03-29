import { baseRequestClient, requestClient } from '#/api/request';

export namespace AuthApi {
  export interface LoginRsaKeyResult {
    algorithm: string;
    expiresAt: number;
    keyId: string;
    publicKey: string;
  }

  /** 登录接口参数 */
  export interface LoginParams {
    encryptedPassword?: string;
    keyId?: string;
    password?: string;
    username?: string;
  }

  /** 登录接口返回值 */
  export interface LoginResult {
    accessToken: string;
  }

  export interface RefreshTokenResult {
    data: string;
    status: number;
  }

  /** 注册接口参数 */
  export interface RegisterParams {
    encryptedPassword?: string;
    keyId?: string;
    password?: string;
    username?: string;
  }

  export interface ChangePasswordParams {
    encryptedNewPassword?: string;
    encryptedOldPassword?: string;
    newKeyId?: string;
    newPassword?: string;
    oldKeyId?: string;
    oldPassword?: string;
  }
}

/**
 * 登录
 */
export async function loginApi(data: AuthApi.LoginParams) {
  return requestClient.post<AuthApi.LoginResult>('/auth/login', data, {
    withCredentials: true,
  });
}

/**
 * 获取登录 RSA 临时公钥
 */
export async function getLoginRsaKeyApi() {
  return requestClient.get<AuthApi.LoginRsaKeyResult>('/auth/rsa', {
    withCredentials: true,
  });
}

/**
 * 注册
 */
export async function registerApi(data: AuthApi.RegisterParams) {
  return requestClient.post('/auth/register', data, {
    withCredentials: true,
  });
}

/**
 * 修改密码（需要已登录）
 */
export async function changePasswordApi(data: AuthApi.ChangePasswordParams) {
  return requestClient.post('/auth/password', data, {
    withCredentials: true,
  });
}

/**
 * 刷新accessToken
 */
export async function refreshTokenApi() {
  return baseRequestClient.post<AuthApi.RefreshTokenResult>(
    '/auth/refresh',
    null,
    {
      withCredentials: true,
    },
  );
}

/**
 * 退出登录
 */
export async function logoutApi() {
  return baseRequestClient.post('/auth/logout', null, {
    withCredentials: true,
  });
}

/**
 * 获取用户权限码
 */
export async function getAccessCodesApi() {
  return requestClient.get<string[]>('/auth/codes');
}
