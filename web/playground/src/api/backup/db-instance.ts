import { requestClient } from '#/api/request';

export namespace DbInstanceApi {
  export interface DbInstance {
    database: string;
    host: string;
    id: string;
    name: string;
    password: string;
    port: number;
    user: string;
  }
}

async function getDbInstanceList(keyword?: string) {
  return requestClient.get<DbInstanceApi.DbInstance[]>('/db-instances', {
    params: { keyword },
  });
}

async function createDbInstance(
  data: Omit<DbInstanceApi.DbInstance, 'id'> & { id?: string },
) {
  return requestClient.post<DbInstanceApi.DbInstance>('/db-instances', data);
}

async function updateDbInstance(
  id: string,
  data: Omit<DbInstanceApi.DbInstance, 'id'>,
) {
  return requestClient.put<DbInstanceApi.DbInstance>(
    `/db-instances/${id}`,
    data,
  );
}

async function deleteDbInstance(id: string) {
  return requestClient.delete(`/db-instances/${id}`);
}

/** POST /api/db-instances/test-connection 校验当前连接信息能否访问 MySQL */
export namespace DbInstanceTestConnectionApi {
  export interface Payload {
    database: string;
    host: string;
    password: string;
    port: number;
    user: string;
  }
}

async function testDbConnection(data: DbInstanceTestConnectionApi.Payload) {
  return requestClient.post<{ ok: boolean }>(
    '/db-instances/test-connection',
    data,
  );
}

/** POST /api/db-instances/:id/backup 立即执行 mydumper 全量备份 */
export namespace DbInstanceBackupApi {
  export interface RunBackupPayload {
    /** 备份根目录，对应脚本 -b，不传则用服务端默认 */
    backup_dir?: string;
    backup_type?: 'full' | 'increment';
    /** 对应脚本 -c */
    clean_days?: number;
    compress?: string;
    /** 增量备份时必填，关联的全量备份记录 id */
    full_backup_file_id?: string;
    /** 逗号分隔，对应脚本 -i */
    ignore_tables?: string;
    /** 定时任务触发时由后端传入 */
    job_id?: string;
    /** 逗号分隔，对应脚本 -t */
    tables?: string;
    threads?: number;
    timeout_seconds?: number;
  }

  export interface RunBackupResult {
    /** 后台执行时为 true，接口立即返回 */
    async?: boolean;
    backup_dir?: string;
    database?: string;
    dir_name?: string;
    exit_code?: number;
    host?: string;
    instance_id: string;
    pending?: Record<string, unknown>;
    port?: number;
    stderr_tail?: string;
    stdout_tail?: string;
  }
}

async function runDbInstanceBackup(
  id: string,
  data?: DbInstanceBackupApi.RunBackupPayload,
) {
  return requestClient.post<DbInstanceBackupApi.RunBackupResult>(
    `/db-instances/${id}/backup`,
    data ?? {},
  );
}

/** POST /api/db-instances/:id/restore 使用 myloader 还原 */
export namespace DbInstanceRestoreApi {
  export interface RunRestorePayload {
    /** 是否自动回放该全量基线下的增量，默认 true */
    apply_incrementals?: boolean;
    /** 备份文件列表中的目录名 */
    dir_name: string;
    /** 如 DROP、NONE，默认 DROP */
    drop_table_mode?: string;
    ignore_tables?: string;
    /** 可选：指定回放到该增量目录（含） */
    incremental_dir_name?: string;
    /** 备份内源库名，默认备份记录里的 database */
    source_database?: string;
    tables?: string;
    /** 目标库名，默认实例 database */
    target_database?: string;
    threads?: number;
    timeout_seconds?: number;
  }

  export interface RunRestoreResult {
    applied_increment_count?: number;
    apply_incrementals?: boolean;
    /** 后台执行时为 true，接口立即返回 */
    async?: boolean;
    dir_name: string;
    exit_code?: number;
    instance_id: string;
    source_database: string;
    source_dir: string;
    stderr_tail?: string;
    stdout_tail?: string;
    target_database: string;
  }
}

async function runDbInstanceRestore(
  id: string,
  data: DbInstanceRestoreApi.RunRestorePayload,
) {
  return requestClient.post<DbInstanceRestoreApi.RunRestoreResult>(
    `/db-instances/${id}/restore`,
    data,
  );
}

export {
  createDbInstance,
  deleteDbInstance,
  getDbInstanceList,
  runDbInstanceBackup,
  runDbInstanceRestore,
  testDbConnection,
  updateDbInstance,
};
