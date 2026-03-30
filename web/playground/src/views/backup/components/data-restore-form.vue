<script lang="ts" setup>
import type { BackupFilesApi } from '#/api/backup/backup-files';
import type { DbInstanceApi } from '#/api/backup/db-instance';

import { onMounted, ref } from 'vue';

import { Button, message } from 'ant-design-vue';

import { useVbenForm, z } from '#/adapter/form';
import { getBackupFilesList } from '#/api/backup/backup-files';
import {
  getDbInstanceList,
  runDbInstanceRestore,
  testDbConnection,
} from '#/api/backup/db-instance';
import { $t } from '#/locales';

const props = withDefaults(
  defineProps<{
    hideFooter?: boolean;
    /** 预填目标库名，默认与备份记录中的库名一致 */
    initialDatabase?: string;
    /** 从备份列表行打开时预选的备份目录 */
    initialDirName?: string;
  }>(),
  {
    hideFooter: false,
    initialDirName: undefined,
    initialDatabase: undefined,
  },
);

const emit = defineEmits<{
  /** 还原接口调用成功 */
  success: [];
}>();

const instances = ref<DbInstanceApi.DbInstance[]>([]);
const backupFiles = ref<BackupFilesApi.BackupFile[]>([]);
const restoreLoading = ref(false);
const testConnectionLoading = ref(false);
/** 已提交后台还原任务后锁定「执行还原」 */
const executeLocked = ref(false);

function formatBytes(n: number) {
  if (n === undefined || n === null || Number.isNaN(Number(n))) return '-';
  const v = Number(n);
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(2)} KB`;
  return `${(v / 1024 / 1024).toFixed(2)} MB`;
}

function getFullBackups() {
  return backupFiles.value.filter((f) => (f.backup_type || 'full') === 'full');
}

function getIncrementBackupsByFullDirName(fullDirName?: string) {
  const fullDir = (fullDirName || '').trim();
  if (!fullDir) return [] as BackupFilesApi.BackupFile[];
  const full = getFullBackups().find(
    (f) => (f.dirName || '').trim() === fullDir,
  );
  const fullId = (full?.backup_file_id || '').trim();
  if (!fullId) return [] as BackupFilesApi.BackupFile[];
  return backupFiles.value
    .filter(
      (f) =>
        (f.backup_type || 'full') === 'increment' &&
        (f.full_backup_file_id || '').trim() === fullId,
    )
    .sort((a, b) => (b.backupTime || '').localeCompare(a.backupTime || ''));
}

function updateIncrementOptions(fullDirName?: string) {
  const increments = getIncrementBackupsByFullDirName(fullDirName);
  formApi.updateSchema([
    {
      componentProps: {
        options: increments.map((f) => ({
          label: `${f.dirName} · ${f.backupTime} · ${formatBytes(f.size)}`,
          value: f.dirName,
        })),
      },
      fieldName: 'incrementalDirName',
    },
  ]);
}

const [RestoreForm, formApi] = useVbenForm({
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
          'page.backup.backupFilesPage.dataRestoreForm.dbInstancePlaceholder',
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
      label: $t('page.backup.backupFilesPage.dataRestoreForm.dbInstanceLabel'),
    },
    {
      component: 'Select',
      componentProps: {
        allowClear: !props.initialDirName,
        filterOption: true,
        options: [],
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.backupFilePlaceholder',
        ),
        showSearch: true,
        disabled: !!props.initialDirName,
      },
      dependencies: {
        trigger(values, form) {
          const dir = values.dirName as string | undefined;
          updateIncrementOptions(dir);
          if (!dir) {
            form.setFieldValue('incrementalDirName', undefined);
            return;
          }
          const row = backupFiles.value.find((f) => f.dirName === dir);
          if (!row) return;
          form.setFieldValue('database', row.database);
          const selectedInc = (
            values.incrementalDirName as string | undefined
          )?.trim();
          if (
            selectedInc &&
            !getIncrementBackupsByFullDirName(dir).some(
              (x) => (x.dirName || '').trim() === selectedInc,
            )
          ) {
            form.setFieldValue('incrementalDirName', undefined);
          }
        },
        triggerFields: ['dirName'],
      },
      fieldName: 'dirName',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t('page.backup.backupFilesPage.dataRestoreForm.backupFileLabel'),
      rules: 'required',
    },
    {
      component: 'Select',
      componentProps: {
        allowClear: true,
        filterOption: true,
        options: [],
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.incrementalBackupFilePlaceholder',
        ),
        showSearch: true,
      },
      fieldName: 'incrementalDirName',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t(
        'page.backup.backupFilesPage.dataRestoreForm.incrementalBackupFileLabel',
      ),
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.targetDbPlaceholder',
        ),
      },
      fieldName: 'database',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t('page.backup.backupFilesPage.dataRestoreForm.targetDbLabel'),
      rules: 'required',
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.hostPlaceholder',
        ),
      },
      defaultValue: '127.0.0.1',
      fieldName: 'host',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataRestoreForm.hostLabel'),
      rules: 'required',
    },
    {
      component: 'InputNumber',
      componentProps: {
        max: 65_535,
        min: 1,
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.portPlaceholder',
        ),
      },
      defaultValue: 3306,
      fieldName: 'port',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataRestoreForm.portLabel'),
      rules: z.coerce
        .number({
          invalid_type_error: $t(
            'page.backup.backupFilesPage.dataRestoreForm.invalidPort',
          ),
        })
        .min(1)
        .max(65_535),
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.userPlaceholder',
        ),
      },
      defaultValue: 'root',
      fieldName: 'user',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataRestoreForm.userLabel'),
      rules: 'required',
    },
    {
      component: 'InputPassword',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.passwordPlaceholder',
        ),
      },
      fieldName: 'password',
      formItemClass: 'col-span-1',
      label: $t('page.backup.backupFilesPage.dataRestoreForm.passwordLabel'),
      rules: 'required',
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.tablesPlaceholder',
        ),
      },
      fieldName: 'tables',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t('page.backup.backupFilesPage.dataRestoreForm.tablesLabel'),
    },
    {
      component: 'Input',
      componentProps: {
        placeholder: $t(
          'page.backup.backupFilesPage.dataRestoreForm.ignoreTablesPlaceholder',
        ),
      },
      fieldName: 'ignoreTables',
      formItemClass: 'col-span-1 md:col-span-2',
      label: $t(
        'page.backup.backupFilesPage.dataRestoreForm.ignoreTablesLabel',
      ),
    },
  ],
});

async function loadBackupFiles() {
  try {
    backupFiles.value = await getBackupFilesList();
  } catch {
    backupFiles.value = [];
  }
  const fullBackups = getFullBackups();
  const fixedFullDir = props.initialDirName?.trim() || '';
  const fullForOptions = fixedFullDir
    ? fullBackups.filter((f) => (f.dirName || '').trim() === fixedFullDir)
    : fullBackups;
  formApi.updateSchema([
    {
      componentProps: {
        options: fullForOptions.map((f) => ({
          label: `${f.dirName} · ${f.database} · ${f.backupTime} · ${formatBytes(f.size)}`,
          value: f.dirName,
        })),
      },
      fieldName: 'dirName',
    },
  ]);
  updateIncrementOptions(fixedFullDir);
}

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

async function applyInitialFromProps() {
  const dir = props.initialDirName?.trim();
  if (!dir) return;
  const row = backupFiles.value.find((f) => f.dirName === dir);
  const db = props.initialDatabase?.trim() || row?.database || '';
  await formApi.setFieldValue('dirName', dir);
  await formApi.setFieldValue('incrementalDirName', undefined);
  await formApi.setFieldValue('database', db);
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
        'page.backup.backupFilesPage.dataRestoreForm.warningFillHostPortUserTargetDb',
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
      $t('page.backup.backupFilesPage.dataRestoreForm.connectSuccess'),
    );
  } catch {
    // 失败提示由 request 拦截器处理
  } finally {
    testConnectionLoading.value = false;
  }
}

async function onExecuteRestore() {
  const { valid } = await formApi.validate();
  if (!valid) return;
  const values = await formApi.getValues();

  const instanceId = (values.dbInstanceId as string | undefined)?.trim();
  if (!instanceId) {
    message.warning(
      $t('page.backup.backupFilesPage.dataRestoreForm.warningSelectDbInstance'),
    );
    return;
  }

  const dirName = (values.dirName as string | undefined)?.trim();
  if (!dirName) {
    message.warning(
      $t('page.backup.backupFilesPage.dataRestoreForm.warningSelectBackupFile'),
    );
    return;
  }

  const targetDb = (values.database as string | undefined)?.trim();
  if (!targetDb) {
    message.warning(
      $t('page.backup.backupFilesPage.dataRestoreForm.warningFillTargetDbName'),
    );
    return;
  }

  const tables = (values.tables as string | undefined)?.trim();
  const ignoreTables = (values.ignoreTables as string | undefined)?.trim();
  const incrementalDirName = (
    values.incrementalDirName as string | undefined
  )?.trim();

  restoreLoading.value = true;
  try {
    await runDbInstanceRestore(instanceId, {
      apply_incrementals: !!incrementalDirName,
      dir_name: dirName,
      incremental_dir_name: incrementalDirName || undefined,
      target_database: targetDb,
      tables: tables || undefined,
      ignore_tables: ignoreTables || undefined,
    });
    message.success(
      $t(
        'page.backup.backupFilesPage.dataRestoreForm.successSubmittingRestore',
      ),
    );
    executeLocked.value = true;
    emit('success');
  } catch {
    // 错误由 request 拦截器提示
  } finally {
    restoreLoading.value = false;
  }
}

async function bootstrap() {
  await loadInstances();
  await loadBackupFiles();
  await applyInitialFromProps();
}

function resetExecuteLock() {
  executeLocked.value = false;
}

onMounted(() => {
  void bootstrap();
});

defineExpose({
  reloadOptions: async () => {
    await bootstrap();
  },
  testConnection: onTestConnection,
  executeRestore: onExecuteRestore,
  testConnectionLoading,
  restoreLoading,
  executeLocked,
  resetExecuteLock,
});
</script>

<template>
  <div class="data-restore-form">
    <p class="mb-4 text-sm text-gray-500">
      {{ $t('page.backup.backupFilesPage.dataRestoreForm.notice') }}
    </p>
    <RestoreForm />
    <div
      v-if="!hideFooter"
      class="mt-4 flex flex-wrap justify-end gap-3 border-t border-border pt-4"
    >
      <Button :loading="testConnectionLoading" @click="onTestConnection">
        {{ $t('page.backup.backupFilesPage.dataRestoreForm.testConnection') }}
      </Button>
      <Button
        :disabled="executeLocked"
        :loading="restoreLoading"
        type="primary"
        @click="onExecuteRestore"
      >
        {{ $t('page.backup.backupFilesPage.dataRestoreForm.executeRestore') }}
      </Button>
      <Button v-if="executeLocked" type="link" @click="resetExecuteLock">
        {{
          $t(
            'page.backup.backupFilesPage.dataRestoreForm.allowAgainExecuteRestore',
          )
        }}
      </Button>
    </div>
  </div>
</template>
