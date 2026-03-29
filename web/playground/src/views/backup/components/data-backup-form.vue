<script lang="ts" setup>
import type { BackupFilesApi } from '#/api/backup/backup-files';
import type { BackupJobsApi } from '#/api/backup/backup-jobs';
import type {
  DbInstanceApi,
  DbInstanceBackupApi,
} from '#/api/backup/db-instance';

import { onMounted, ref } from 'vue';

import { Button, message } from 'ant-design-vue';

import { useVbenForm, z } from '#/adapter/form';
import { getBackupFilesList } from '#/api/backup/backup-files';
import { getBackupJobsList } from '#/api/backup/backup-jobs';
import {
  getDbInstanceList,
  runDbInstanceBackup,
  testDbConnection,
} from '#/api/backup/db-instance';
import { $t } from '#/locales';

type JobRow = BackupJobsApi.BackupJob;

defineProps<{
  /** 由外部抽屉统一渲染底部按钮时，隐藏本组件 footer */
  hideFooter?: boolean;
}>();

const emit = defineEmits<{
  /** 立即备份接口调用成功 */
  success: [payload?: { dirName?: string }];
}>();

const instances = ref<DbInstanceApi.DbInstance[]>([]);
const allBackupJobs = ref<JobRow[]>([]);
const allBackupFiles = ref<BackupFilesApi.BackupFile[]>([]);
const backupLoading = ref(false);
const testConnectionLoading = ref(false);
/** 已提交后台备份任务后锁定「执行备份」，避免重复点击 */
const executeLocked = ref(false);
/** 从“增量记录 -> 开始备份”进入时，表单字段只读 */
const incrementPresetLocked = ref(false);

function setFormEditable(editable: boolean) {
  const disabled = !editable;
  formApi.updateSchema([
    { fieldName: 'dbInstanceId', componentProps: { disabled } },
    { fieldName: 'backupType', componentProps: { disabled } },
    { fieldName: 'fullBackupFileId', componentProps: { disabled } },
    { fieldName: 'host', componentProps: { disabled } },
    { fieldName: 'port', componentProps: { disabled } },
    { fieldName: 'user', componentProps: { disabled } },
    { fieldName: 'password', componentProps: { disabled } },
    { fieldName: 'database', componentProps: { disabled } },
    { fieldName: 'cleanDays', componentProps: { disabled } },
    { fieldName: 'tables', componentProps: { disabled } },
    { fieldName: 'ignoreTables', componentProps: { disabled } },
  ]);
}

function getLinkedFullSelectOptions() {
  return allBackupFiles.value
    .filter((f) => (f.backup_type || 'full') === 'full')
    .map((f) => ({
      label: `${f.dirName} (${f.backupTime})`,
      value: f.backup_file_id || '',
    }))
    .filter((x) => x.value);
}

const [BackupForm, formApi] = useVbenForm({
  commonConfig: {
    colon: true,
    hideRequiredMark: true,
    componentProps: {
      class: 'w-full',
    },
  },
  layout: 'horizontal',
  showDefaultActions: false,
  wrapperClass: 'grid-cols-1 md:grid-cols-2 gap-x-4',
  schema: [
    {
      component: 'Select',
      componentProps: {
        allowClear: true,
        filterOption: true,
        options: [],
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.dbInstancePlaceholder',
        ),
        showSearch: true,
      },
      dependencies: {
        trigger(values, form) {
          const id = values.dbInstanceId;
          if (!id) return;
          const inst = instances.value.find((i) => i.id === id);
          if (!inst) return;
          form.setFieldValue('host', inst.host);
          form.setFieldValue('port', inst.port);
          form.setFieldValue('user', inst.user);
          form.setFieldValue('password', inst.password);
          form.setFieldValue('database', inst.database);
        },
        triggerFields: ['dbInstanceId'],
      },
      fieldName: 'dbInstanceId',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t('page.backup.backupFilesPage.dataBackupForm.dbInstanceLabel'),
    },
    {
      component: 'RadioGroup',
      componentProps: {
        buttonStyle: 'solid',
        optionType: 'button',
        options: [
          {
            label: $t(
              'page.backup.backupFilesPage.dataBackupForm.backupTypeFull',
            ),
            value: 'full',
          },
          {
            label: $t(
              'page.backup.backupFilesPage.dataBackupForm.backupTypeIncremental',
            ),
            value: 'incremental',
          },
        ],
      },
      defaultValue: 'full',
      dependencies: {
        trigger(values, form) {
          if (values.backupType !== 'incremental') {
            form.setFieldValue('fullBackupFileId', undefined);
          }
        },
        triggerFields: ['backupType'],
      },
      fieldName: 'backupType',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t('page.backup.backupFilesPage.dataBackupForm.backupTypeLabel'),
    },
    {
      component: 'Select',
      componentProps: {
        allowClear: true,
        filterOption: true,
        options: [],
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.linkedFullJobPlaceholder',
        ),
        showSearch: true,
      },
      dependencies: {
        required(values) {
          return values.backupType === 'incremental';
        },
        show(values) {
          return values.backupType === 'incremental';
        },
        triggerFields: ['backupType'],
      },
      fieldName: 'fullBackupFileId',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t(
        'page.backup.backupFilesPage.dataBackupForm.linkedFullJobLabel',
      ),
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.hostPlaceholder',
        ),
      },
      defaultValue: '127.0.0.1',
      fieldName: 'host',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataBackupForm.hostLabel'),
      rules: 'required',
    },
    {
      component: 'InputNumber',
      componentProps: {
        max: 65_535,
        min: 1,
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.portPlaceholder',
        ),
      },
      defaultValue: 3306,
      fieldName: 'port',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataBackupForm.portLabel'),
      rules: z.coerce
        .number({
          invalid_type_error: $t(
            'page.backup.backupFilesPage.dataBackupForm.invalidPort',
          ),
        })
        .min(1)
        .max(65_535),
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.userPlaceholder',
        ),
      },
      defaultValue: 'root',
      fieldName: 'user',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataBackupForm.userLabel'),
      rules: 'required',
    },
    {
      component: 'InputPassword',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.passwordPlaceholder',
        ),
      },
      fieldName: 'password',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataBackupForm.passwordLabel'),
      rules: 'required',
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.databasePlaceholder',
        ),
      },
      fieldName: 'database',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataBackupForm.databaseLabel'),
      rules: 'required',
    },
    {
      component: 'InputNumber',
      componentProps: {
        min: 0,
        placeholder: '0',
      },
      defaultValue: 0,
      fieldName: 'cleanDays',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataBackupForm.cleanDaysLabel'),
      rules: z.coerce.number().int().min(0),
      suffix: () =>
        $t('page.backup.backupFilesPage.dataBackupForm.cleanDaysSuffix'),
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.tablesPlaceholder',
        ),
      },
      fieldName: 'tables',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t('page.backup.backupFilesPage.dataBackupForm.tablesLabel'),
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataBackupForm.ignoreTablesPlaceholder',
        ),
      },
      fieldName: 'ignoreTables',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t('page.backup.backupFilesPage.dataBackupForm.ignoreTablesLabel'),
    },
  ],
});

async function loadInstances() {
  try {
    instances.value = await getDbInstanceList();
  } catch {
    instances.value = [];
  }
  formApi.updateSchema([
    {
      componentProps: {
        options: instances.value.map((i) => ({
          label: `${i.name}（${i.host} / ${i.database}）`,
          value: i.id,
        })),
      },
      fieldName: 'dbInstanceId',
    },
  ]);
}

async function loadBackupJobs() {
  try {
    allBackupJobs.value = await getBackupJobsList();
  } catch {
    allBackupJobs.value = [];
  }
  formApi.updateSchema([
    {
      componentProps: {
        options: getLinkedFullSelectOptions(),
      },
      fieldName: 'fullBackupFileId',
    },
  ]);
}

async function loadBackupFiles() {
  try {
    allBackupFiles.value = await getBackupFilesList();
  } catch {
    allBackupFiles.value = [];
  }
  formApi.updateSchema([
    {
      componentProps: {
        options: getLinkedFullSelectOptions(),
      },
      fieldName: 'fullBackupFileId',
    },
  ]);
}

async function onTestConnection() {
  const values = await formApi.getValues();
  const host = String(values.host ?? '').trim();
  const port = values.port;
  const user = String(values.user ?? '').trim();
  const password = String(values.password ?? '');
  const database = String(values.database ?? '').trim();
  if (
    !host ||
    port === null ||
    port === undefined ||
    port === '' ||
    !user ||
    !database
  ) {
    message.warning(
      $t(
        'page.backup.backupFilesPage.dataBackupForm.warningFillHostPortUserDb',
      ),
    );
    return;
  }

  testConnectionLoading.value = true;
  try {
    await testDbConnection({
      host,
      port: Number(port),
      user,
      password,
      database,
    });
    message.success(
      $t('page.backup.backupFilesPage.dataBackupForm.connectSuccess'),
    );
  } catch {
    // 失败提示由 request 拦截器处理
  } finally {
    testConnectionLoading.value = false;
  }
}

async function onExecuteBackup() {
  const { valid } = await formApi.validate();
  if (!valid) return;
  const values = await formApi.getValues();

  const instanceId = (values.dbInstanceId as string | undefined)?.trim();
  if (!instanceId) {
    message.warning(
      $t('page.backup.backupFilesPage.dataBackupForm.warningSelectDbInstance'),
    );
    return;
  }

  const payload: DbInstanceBackupApi.RunBackupPayload = {
    backup_type: values.backupType === 'incremental' ? 'increment' : 'full',
  };
  if (payload.backup_type === 'increment') {
    const fullBackupFileId = (
      values.fullBackupFileId as string | undefined
    )?.trim();
    if (!fullBackupFileId) {
      message.warning(
        $t(
          'page.backup.backupFilesPage.dataBackupForm.warningSelectLinkedFull',
        ),
      );
      return;
    }
    payload.full_backup_file_id = fullBackupFileId;
  }
  const cd = values.cleanDays;
  if (cd !== undefined && cd !== null && cd !== '') {
    const n = Number(cd);
    if (!Number.isNaN(n)) payload.clean_days = n;
  }
  const tables = (values.tables as string | undefined)?.trim();
  if (tables) payload.tables = tables;
  const ignoreTables = (values.ignoreTables as string | undefined)?.trim();
  if (ignoreTables) payload.ignore_tables = ignoreTables;

  backupLoading.value = true;
  try {
    const result = await runDbInstanceBackup(instanceId, payload);
    // 后端会先写入一条 size=0 的全量记录，再后台执行；
    // 这里立即刷新下拉选项，确保同页可直接发起增量备份，无需手动刷新页面。
    await loadBackupFiles();
    const dirName =
      (result?.dir_name || '').trim() ||
      String((result as any)?.pending?.dirName || '').trim();
    message.success(
      $t('page.backup.backupFilesPage.dataBackupForm.successSubmittingBackup'),
    );
    executeLocked.value = true;
    emit('success', dirName ? { dirName } : undefined);
  } catch {
    // 错误提示由 request 拦截器处理
  } finally {
    backupLoading.value = false;
  }
}

onMounted(async () => {
  await loadInstances();
  await loadBackupJobs();
  await loadBackupFiles();
  setFormEditable(true);
});

function resetExecuteLock() {
  executeLocked.value = false;
}

function resetIncrementPresetLock() {
  incrementPresetLocked.value = false;
  setFormEditable(true);
}

async function prepareIncrementBackupFromFull(full: BackupFilesApi.BackupFile) {
  const fullBackupFileId = (full.backup_file_id || '').trim();
  if (!fullBackupFileId) return;

  incrementPresetLocked.value = true;
  await loadInstances();
  await loadBackupFiles();

  const fullDbInstanceId = (full.db_instance_id || '').trim();
  const matchedInstance =
    instances.value.find((i) => (i.id || '').trim() === fullDbInstanceId) ||
    instances.value.find(
      (i) => (i.database || '').trim() === (full.database || '').trim(),
    );
  const patch: Record<string, any> = {
    backupType: 'incremental',
    fullBackupFileId,
  };
  if (matchedInstance) {
    patch.dbInstanceId = matchedInstance.id;
    patch.host = matchedInstance.host;
    patch.port = matchedInstance.port;
    patch.user = matchedInstance.user;
    patch.password = matchedInstance.password;
    patch.database = matchedInstance.database;
  }
  formApi.setValues(patch);
  setFormEditable(false);
}

defineExpose({
  reloadOptions: async () => {
    await loadInstances();
    await loadBackupJobs();
    await loadBackupFiles();
  },
  prepareIncrementBackupFromFull,
  resetIncrementPresetLock,
  resetExecuteLock,
  testConnection: onTestConnection,
  executeBackup: onExecuteBackup,
  testConnectionLoading,
  backupLoading,
  executeLocked,
});
</script>

<template>
  <div class="data-backup-form">
    <div class="data-backup-form__content">
      <BackupForm />
    </div>
    <div
      v-if="!hideFooter"
      class="data-backup-form__footer mt-auto flex flex-wrap justify-end gap-3 border-t border-border bg-background p-2 px-3"
    >
      <Button :loading="testConnectionLoading" @click="onTestConnection">
        {{ $t('page.backup.backupFilesPage.dataBackupForm.testConnection') }}
      </Button>
      <Button
        :disabled="executeLocked"
        :loading="backupLoading"
        type="primary"
        @click="onExecuteBackup"
      >
        {{ $t('page.backup.backupFilesPage.dataBackupForm.executeBackup') }}
      </Button>
      <Button v-if="executeLocked" type="link" @click="resetExecuteLock">
        {{
          $t(
            'page.backup.backupFilesPage.dataBackupForm.allowAgainExecuteBackup',
          )
        }}
      </Button>
    </div>
  </div>
</template>

<style scoped>
.data-backup-form {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.data-backup-form__content {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.data-backup-form__footer {
  flex-shrink: 0;
}
</style>
