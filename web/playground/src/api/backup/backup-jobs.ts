import { requestClient } from '#/api/request';

export namespace BackupJobsApi {
  export interface BackupJob {
    backup_type: string;
    clean_days: number;
    created_at: string;
    /** 关联的数据库实例 id（与 db-instances 配置一致；旧数据可能为空） */
    db_instance_id?: string;
    enable_gzip: boolean;
    enabled: boolean;
    id: string;
    ignore_tables: string;
    last_run_at: string;
    /** 增量任务的关联全量任务 job id（全量任务为空字符串或未提供） */
    linked_full_backup_job_id?: string;
    name: string;
    schedule: string;
    tables: string;
  }

  export interface BackupJobLogDetail {
    jobLog: string;
    jobLogPresent: boolean;
  }
}

async function getBackupJobsList(keyword?: string) {
  return requestClient.get<BackupJobsApi.BackupJob[]>('/backup-jobs', {
    params: { keyword },
  });
}

async function createBackupJob(
  data: Omit<
    BackupJobsApi.BackupJob,
    'created_at' | 'enable_gzip' | 'enabled' | 'id' | 'last_run_at'
  >,
) {
  return requestClient.post<BackupJobsApi.BackupJob>('/backup-jobs', data);
}

async function updateBackupJob(
  id: string,
  data: Omit<
    BackupJobsApi.BackupJob,
    'created_at' | 'enable_gzip' | 'enabled' | 'id' | 'last_run_at'
  >,
) {
  return requestClient.put<BackupJobsApi.BackupJob>(`/backup-jobs/${id}`, data);
}

async function deleteBackupJob(id: string) {
  return requestClient.post(`/backup-jobs/delete/${id}`);
}

async function runBackupJob(id: string) {
  return requestClient.post(`/backup-jobs/${id}/run`);
}

async function stopBackupJob(id: string) {
  return requestClient.post(`/backup-jobs/${id}/stop`);
}

async function getBackupJobLog(jobId: string) {
  return requestClient.get<BackupJobsApi.BackupJobLogDetail>(
    `/backup-jobs/${jobId}/log`,
  );
}

export {
  createBackupJob,
  deleteBackupJob,
  getBackupJobLog,
  getBackupJobsList,
  runBackupJob,
  stopBackupJob,
  updateBackupJob,
};
