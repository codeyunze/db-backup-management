<script lang="ts" setup>
import type { VbenFormProps } from '#/adapter/form';
import type { VxeTableGridOptions } from '#/adapter/vxe-table';
import type { BackupJobsApi } from '#/api/backup/backup-jobs';
import type { DbInstanceApi } from '#/api/backup/db-instance';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import { Page, useVbenDrawer } from '@vben/common-ui';

import {
  Drawer as AntDrawer,
  Button,
  Form,
  Input,
  InputNumber,
  message,
  Select,
  Spin,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { useVbenVxeGrid } from '#/adapter/vxe-table';
import {
  createBackupJob,
  deleteBackupJob,
  getBackupJobLog,
  getBackupJobsList,
  runBackupJob,
  stopBackupJob,
  updateBackupJob,
} from '#/api/backup/backup-jobs';
import { getDbInstanceList } from '#/api/backup/db-instance';
import { $t } from '#/locales';
import {
  computeNextCronRuns,
  validateCronExpression,
} from '#/utils/cron-preview';

type RowType = BackupJobsApi.BackupJob;

/** 新增任务时默认：每小时整点执行 */
const DEFAULT_CRON = '0 * * * *';

const allDbInstances = ref<DbInstanceApi.DbInstance[]>([]);

const formOptions: VbenFormProps = {
  collapsed: false,
  schema: [
    {
      component: 'Input',
      fieldName: 'keyword',
      label: $t('page.backup.jobSchedulePage.keyword'),
      componentProps: {
        placeholder: $t('page.backup.jobSchedulePage.keywordPlaceholder'),
      },
    },
  ],
  showCollapseButton: false,
  submitOnChange: true,
  submitOnEnter: true,
};

function onActionClick({ code, row }: { code: string; row: RowType }) {
  switch (code) {
    case 'delete': {
      // 删除走 POST /api/backup-jobs/delete/<id>（避免部分环境下 DELETE 预检/代理异常）
      void deleteBackupJob(row.id)
        .then(async () => {
          message.success($t('page.backup.jobSchedulePage.deleteSuccess'));
          await loadAllBackupJobs();
          gridApi.reload();
        })
        .catch(() => {
          message.error($t('page.backup.jobSchedulePage.deleteFailed'));
        });
      break;
    }
    case 'edit': {
      openEdit(row);
      break;
    }
    case 'log': {
      void openJobLog(row);
      break;
    }
    case 'run': {
      runJob(row);
      break;
    }
    case 'stop': {
      stopJob(row);
      break;
    }
    default: {
      break;
    }
  }
}

async function openJobLog(row: RowType) {
  try {
    jobLogDrawerOpen.value = true;
    jobLogDrawerLoading.value = true;
    jobLogJobId.value = row.id;

    const res = await getBackupJobLog(row.id);
    jobLogDrawerContent.value = res.jobLogPresent
      ? res.jobLog
      : $t('page.backup.jobSchedulePage.emptyLog');
    jobLogDrawerPresent.value = res.jobLogPresent;
  } catch {
    message.error($t('page.backup.jobSchedulePage.loadLogFailed'));
  } finally {
    jobLogDrawerLoading.value = false;
  }
}

const jobLogDrawerOpen = ref(false);
const jobLogDrawerLoading = ref(false);
const jobLogJobId = ref('');
const jobLogDrawerContent = ref('');
const jobLogDrawerPresent = ref(false);

const gridOptions: VxeTableGridOptions<RowType> = {
  columns: [
    { title: $t('page.backup.jobSchedulePage.seq'), type: 'seq', width: 60 },
    {
      field: 'name',
      title: $t('page.backup.jobSchedulePage.taskName'),
      minWidth: 200,
    },
    {
      field: 'db_instance_id',
      formatter: ({ row }) => {
        const id = (row as RowType).db_instance_id;
        if (!id) return '—';
        const inst = allDbInstances.value.find((i) => i.id === id);
        return inst?.name || '—';
      },
      minWidth: 200,
      title: $t('page.backup.jobSchedulePage.dbInstance'),
    },
    {
      field: 'schedule',
      title: $t('page.backup.jobSchedulePage.cron'),
      minWidth: 180,
    },
    {
      align: 'center',
      cellRender: {
        name: 'CellTag',
        options: [
          {
            color: 'success',
            label: $t('page.backup.jobSchedulePage.backupTypeFull'),
            value: 'full',
          },
          {
            color: 'processing',
            label: $t('page.backup.jobSchedulePage.backupTypeIncremental'),
            value: 'incremental',
          },
        ],
      },
      field: 'backup_type',
      title: $t('page.backup.jobSchedulePage.backupType'),
      width: 120,
    },
    {
      field: 'enabled',
      title: $t('page.backup.jobSchedulePage.status'),
      width: 100,
      cellRender: {
        name: 'CellTag',
        options: [
          {
            color: 'success',
            label: $t('page.backup.jobSchedulePage.statusRunning'),
            value: true,
          },
          {
            color: 'default',
            label: $t('page.backup.jobSchedulePage.statusStopped'),
            value: false,
          },
        ],
      },
    },
    {
      field: 'last_run_at',
      title: $t('page.backup.jobSchedulePage.lastRunAt'),
      minWidth: 190,
    },
    {
      align: 'right',
      field: 'operation',
      fixed: 'right',
      headerAlign: 'center',
      showOverflow: false,
      title: $t('page.backup.jobSchedulePage.operations'),
      width: 220,
      cellRender: {
        attrs: {
          nameField: 'name',
          onClick: onActionClick,
        },
        name: 'CellOperation',
        options: [
          {
            code: 'run',
            text: $t('page.backup.jobSchedulePage.actionRun'),
            show: (r: RowType) => !r.enabled,
          },
          {
            code: 'stop',
            text: $t('page.backup.jobSchedulePage.actionStop'),
            show: (r: RowType) => !!r.enabled,
          },
          {
            code: 'edit',
            show: (r: RowType) => !r.enabled,
          },
          // 删除在「运行中」也需可用：后端会同步移除 crontab，勿与编辑共用「仅停止时可点」限制
          {
            code: 'delete',
          },
          {
            code: 'log',
            text: $t('page.backup.jobSchedulePage.actionLog'),
          },
        ],
      },
    },
  ],
  height: 'auto',
  keepSource: true,
  pagerConfig: {},
  rowConfig: {
    keyField: 'id',
  },
  proxyConfig: {
    ajax: {
      query: async ({ page }, formValues) => {
        const items = await getBackupJobsList(formValues?.keyword);
        return {
          items,
          total: items.length,
          page: page.currentPage,
          pageSize: page.pageSize,
        };
      },
    },
  },
  toolbarConfig: {
    custom: true,
    refresh: true,
    search: true,
    zoom: true,
  },
};

const [Grid, gridApi] = useVbenVxeGrid({
  formOptions,
  gridOptions,
} as any);

const editingId = ref('');
const saving = ref(false);
const formRef = ref();

const allBackupJobs = ref<RowType[]>([]);
const fullBackupJobs = computed(() =>
  allBackupJobs.value.filter(
    (j) => (j.backup_type || '').toLowerCase() === 'full',
  ),
);

/** 已被「其他」增量任务占用的全量 job id（编辑当前任务时排除自身） */
const fullJobIdsLinkedByOtherIncrementals = computed(() => {
  const set = new Set<string>();
  const currentJobId = editingId.value;
  for (const j of allBackupJobs.value) {
    if ((j.backup_type || '').toLowerCase() !== 'incremental') continue;
    if (j.id === currentJobId) continue;
    const linked = (j.linked_full_backup_job_id || '').trim();
    if (linked) set.add(linked);
  }
  return set;
});

/** 可选全量：未被占用，或占用者就是当前表单已选值（便于回显/修正脏数据） */
const selectableFullBackupJobs = computed(() => {
  const blocked = fullJobIdsLinkedByOtherIncrementals.value;
  const currentLinked = (model.linked_full_backup_job_id || '').trim();
  return fullBackupJobs.value.filter(
    (job) => !blocked.has(job.id) || job.id === currentLinked,
  );
});

const model = reactive<
  Omit<RowType, 'created_at' | 'enable_gzip' | 'enabled' | 'id' | 'last_run_at'>
>({
  db_instance_id: '',
  name: '',
  schedule: DEFAULT_CRON,
  backup_type: 'full',
  linked_full_backup_job_id: '',
  tables: '',
  ignore_tables: '',
  clean_days: 1,
});

const currentDbInstance = computed(() => {
  const id = (model.db_instance_id || '').trim();
  if (!id) return null;
  return allDbInstances.value.find((i) => i.id === id) ?? null;
});

type SchedulePreview =
  | { kind: 'empty' }
  | { kind: 'invalid'; message: string }
  | { kind: 'ok'; times: string[] };

/** 接下来 5 次预计运行时间（合法时每行一条） */
const schedulePreview = computed((): SchedulePreview => {
  const expr = model.schedule?.trim() ?? '';
  if (!expr) {
    return { kind: 'empty' };
  }
  const v = validateCronExpression(expr);
  if (!v.valid) {
    return { kind: 'invalid', message: v.message };
  }
  const runs = computeNextCronRuns(expr, 5);
  if (!runs?.length) {
    return {
      kind: 'invalid',
      message: $t('page.backup.jobSchedulePage.cronInvalidHint'),
    };
  }
  return {
    kind: 'ok',
    times: runs.map((d) => dayjs(d).format('YYYY-MM-DD HH:mm')),
  };
});

async function validateScheduleCron(_rule: unknown, value: string) {
  const r = validateCronExpression(value ?? '');
  if (!r.valid) {
    throw new Error(r.message);
  }
}

async function validateLinkedFullJob(_rule: unknown, value: string) {
  if ((model.backup_type || '').toLowerCase() !== 'incremental') {
    return;
  }
  const v = (value || '').trim();
  if (!v) return;
  if (fullJobIdsLinkedByOtherIncrementals.value.has(v)) {
    throw new Error($t('page.backup.jobSchedulePage.requiredLinkedFullJob'));
  }
}

const [Drawer, drawerApi] = useVbenDrawer({
  onCancel() {
    drawerApi.close();
  },
  onConfirm: async () => {
    await submit();
  },
  onOpenChange(isOpen) {
    if (!isOpen) return;
    drawerApi.setState({ placement: 'right' });

    const data = drawerApi.getData<any>();
    const isEdit = !!data?.id;

    editingId.value = isEdit ? data.id : '';

    model.db_instance_id = isEdit ? (data.db_instance_id ?? '') : '';
    model.name = isEdit ? data.name : '';
    model.schedule = isEdit ? data.schedule : DEFAULT_CRON;
    model.backup_type = isEdit ? data.backup_type : 'full';
    model.linked_full_backup_job_id = isEdit
      ? data.linked_full_backup_job_id || ''
      : '';
    model.tables = isEdit ? data.tables : '';
    model.ignore_tables = isEdit ? data.ignore_tables : '';
    model.clean_days = isEdit ? data.clean_days : 0;

    // reset validate status
    formRef.value?.clearValidate?.();

    if (allBackupJobs.value.length === 0) {
      loadAllBackupJobs();
    }
    if (allDbInstances.value.length === 0) {
      loadDbInstances();
    }
  },
});

watch(
  () => model.backup_type,
  (backupType) => {
    if ((backupType || '').toLowerCase() !== 'incremental') {
      model.linked_full_backup_job_id = '';
    }
  },
);

async function loadAllBackupJobs() {
  try {
    const jobs = await getBackupJobsList();
    allBackupJobs.value = jobs;
  } catch {
    // 下拉可用性不影响列表查看；这里静默即可
  }
}

async function loadDbInstances() {
  try {
    allDbInstances.value = await getDbInstanceList();
  } catch {
    // 静默
  }
}

function openCreate() {
  drawerApi.setData({});
  drawerApi.open();
}

function openEdit(row: any) {
  drawerApi.setData(row);
  drawerApi.open();
}

async function submit() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  saving.value = true;
  drawerApi.lock();
  try {
    if (editingId.value) {
      await updateBackupJob(editingId.value, model);
      message.success($t('page.backup.jobSchedulePage.editSuccess'));
    } else {
      await createBackupJob(model);
      message.success($t('page.backup.jobSchedulePage.addSuccess'));
    }
    await loadAllBackupJobs();
    gridApi.reload();
    drawerApi.close();
  } finally {
    drawerApi.unlock();
    saving.value = false;
  }
}

async function runJob(row: any) {
  const r = row as RowType;
  await runBackupJob(r.id);
  message.success($t('page.backup.jobSchedulePage.actionRunSuccess'));
  gridApi.reload();
}

async function stopJob(row: any) {
  const r = row as RowType;
  await stopBackupJob(r.id);
  message.success($t('page.backup.jobSchedulePage.actionStopSuccess'));
  gridApi.reload();
}

onMounted(() => {
  gridApi.query();
  loadAllBackupJobs();
  loadDbInstances();
});
</script>

<template>
  <Page auto-content-height>
    <Drawer
      class="w-full max-w-[800px]"
      :title="
        editingId
          ? $t('page.backup.jobSchedulePage.editTitle')
          : $t('page.backup.jobSchedulePage.createTitle')
      "
    >
      <Form
        ref="formRef"
        :model="model"
        layout="horizontal"
        :label-col="{ span: 8 }"
        :wrapper-col="{ span: 16 }"
      >
        <div class="grid grid-cols-2 gap-4">
          <Form.Item
            class="col-span-2"
            :label="$t('page.backup.jobSchedulePage.formTaskName')"
            name="name"
            :rules="[
              {
                required: true,
                message: $t('page.backup.jobSchedulePage.requiredTaskName'),
              },
            ]"
            :label-col="{ span: 4 }"
            :wrapper-col="{ span: 20 }"
          >
            <Input
              v-model:value="model.name"
              :placeholder="
                $t('page.backup.jobSchedulePage.formTaskNamePlaceholder')
              "
            />
          </Form.Item>

          <Form.Item
            :label="$t('page.backup.jobSchedulePage.formDbInstanceInfo')"
            name="db_instance_id"
            :rules="[
              {
                required: true,
                message: $t('page.backup.jobSchedulePage.requiredDbInstance'),
              },
            ]"
          >
            <Select
              v-model:value="model.db_instance_id"
              class="w-full"
              :placeholder="
                $t('page.backup.jobSchedulePage.requiredDbInstance')
              "
              show-search
              option-filter-prop="label"
              :filter-option="
                (input: string, option: any) =>
                  String(option?.label ?? '')
                    .toLowerCase()
                    .includes(input.toLowerCase())
              "
              :options="
                allDbInstances.map((inst) => ({
                  value: inst.id,
                  label: `${inst.name} (${inst.database})`,
                }))
              "
            />
          </Form.Item>

          <Form.Item
            :label="$t('page.backup.jobSchedulePage.formBackupType')"
            name="backup_type"
            :rules="[
              {
                required: true,
                message: $t('page.backup.jobSchedulePage.requiredBackupType'),
              },
            ]"
          >
            <Select v-model:value="model.backup_type" class="w-full">
              <Select.Option value="full">
                {{ $t('page.backup.jobSchedulePage.backupTypeFull') }}
              </Select.Option>
              <Select.Option value="incremental">
                {{ $t('page.backup.jobSchedulePage.backupTypeIncremental') }}
              </Select.Option>
            </Select>
          </Form.Item>

          <div
            v-if="currentDbInstance"
            class="col-span-2 rounded-lg bg-[hsl(var(--muted)/0.35)] p-4 dark:bg-white/5"
          >
            <div class="mb-3 text-sm font-medium text-foreground">
              {{ $t('page.backup.jobSchedulePage.currentDbInstanceInfo') }}
            </div>
            <div
              class="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-3"
            >
              <div>
                <span
                  class="text-muted-foreground"
                  v-text="$t('page.backup.jobSchedulePage.instanceNameLabel')"
                ></span>
                <span>{{ currentDbInstance.name }}</span>
              </div>
              <div>
                <span
                  class="text-muted-foreground"
                  v-text="$t('page.backup.jobSchedulePage.hostLabel')"
                ></span>
                <span>{{ currentDbInstance.host }}</span>
              </div>
              <div>
                <span
                  class="text-muted-foreground"
                  v-text="$t('page.backup.jobSchedulePage.portLabel')"
                ></span>
                <span>{{ currentDbInstance.port }}</span>
              </div>
              <div>
                <span
                  class="text-muted-foreground"
                  v-text="$t('page.backup.jobSchedulePage.userLabel')"
                ></span>
                <span>{{ currentDbInstance.user }}</span>
              </div>
              <div>
                <span
                  class="text-muted-foreground"
                  v-text="$t('page.backup.jobSchedulePage.databaseLabel')"
                ></span>
                <span>{{ currentDbInstance.database }}</span>
              </div>
            </div>
          </div>

          <div
            v-else-if="(model.db_instance_id || '').trim()"
            class="col-span-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
          >
            {{ $t('page.backup.jobSchedulePage.invalidSelectedInstance') }}
          </div>

          <div v-if="model.backup_type === 'incremental'" class="col-span-2">
            <Form.Item
              :label="$t('page.backup.jobSchedulePage.formLinkedFullJob')"
              name="linked_full_backup_job_id"
              :rules="[
                {
                  required: true,
                  message: $t(
                    'page.backup.jobSchedulePage.requiredLinkedFullJob',
                  ),
                },
                {
                  validator: validateLinkedFullJob,
                  trigger: ['change', 'blur'],
                },
              ]"
              :label-col="{ span: 4 }"
              :wrapper-col="{ span: 20 }"
            >
              <Select
                v-model:value="model.linked_full_backup_job_id"
                class="w-full"
                :placeholder="$t('page.backup.jobSchedulePage.selectAll')"
              >
                <Select.Option value="" disabled>
                  {{ $t('page.backup.jobSchedulePage.selectOptionAll') }}
                </Select.Option>
                <Select.Option
                  v-for="job in selectableFullBackupJobs"
                  :key="job.id"
                  :value="job.id"
                >
                  {{ job.name }} ({{ job.id }})
                </Select.Option>
              </Select>
            </Form.Item>
          </div>

          <Form.Item
            :label="$t('page.backup.jobSchedulePage.formCron')"
            name="schedule"
            :rules="[
              {
                required: true,
                message: $t('page.backup.jobSchedulePage.requiredCron'),
              },
              { validator: validateScheduleCron, trigger: ['change', 'blur'] },
            ]"
          >
            <Input
              v-model:value="model.schedule"
              :placeholder="
                $t('page.backup.jobSchedulePage.cronExamplePlaceholder')
              "
            />
            <div class="mt-1 text-xs leading-relaxed text-[#8c959f]">
              <template v-if="schedulePreview.kind === 'empty'">
                {{ $t('page.backup.jobSchedulePage.cronNotSetHint') }}
              </template>
              <template v-else-if="schedulePreview.kind === 'invalid'">
                {{ schedulePreview.message }}
              </template>
              <template v-else>
                <div class="mb-0.5">
                  {{ $t('page.backup.jobSchedulePage.lastRunTimesHint') }}
                </div>
                <div
                  v-for="(t, i) in schedulePreview.times"
                  :key="i"
                  class="leading-5"
                >
                  {{ t }}
                </div>
              </template>
            </div>
          </Form.Item>

          <Form.Item
            :label="$t('page.backup.jobSchedulePage.cleanDays')"
            name="clean_days"
            :rules="[
              {
                required: true,
                message: $t(
                  'page.backup.jobSchedulePage.requiredCleanDaysHint',
                ),
              },
            ]"
          >
            <InputNumber
              v-model:value="model.clean_days"
              :min="0"
              class="w-full"
            />
          </Form.Item>

          <div class="col-span-2">
            <Form.Item
              :label="$t('page.backup.jobSchedulePage.onlyBackupTables')"
              name="tables"
              :rules="[]"
              :label-col="{ span: 4 }"
              :wrapper-col="{ span: 20 }"
            >
              <Input
                v-model:value="model.tables"
                :placeholder="
                  $t('page.backup.jobSchedulePage.onlyBackupTablesPlaceholder')
                "
                class="w-full"
              />
            </Form.Item>
          </div>

          <div class="col-span-2">
            <Form.Item
              :label="$t('page.backup.jobSchedulePage.ignoreTables')"
              name="ignore_tables"
              :label-col="{ span: 4 }"
              :wrapper-col="{ span: 20 }"
            >
              <Input
                v-model:value="model.ignore_tables"
                :placeholder="
                  $t('page.backup.jobSchedulePage.ignoreTablesPlaceholder')
                "
                class="w-full"
              />
            </Form.Item>
          </div>
        </div>
      </Form>
    </Drawer>

    <AntDrawer
      v-model:open="jobLogDrawerOpen"
      placement="right"
      :title="$t('page.backup.jobSchedulePage.drawerLogTitle', [jobLogJobId])"
      :width="800"
      destroy-on-close
    >
      <Spin :spinning="jobLogDrawerLoading">
        <pre
          style="
            margin: 0;
            font-size: 12px;
            line-height: 1.5;
            word-break: break-word;
            white-space: pre-wrap;
          "
        >
          {{ jobLogDrawerContent }}
        </pre>
      </Spin>
    </AntDrawer>

    <Grid :table-title="$t('page.backup.jobSchedule')">
      <template #toolbar-tools>
        <Button type="primary" @click="openCreate">
          {{ $t('page.backup.jobSchedulePage.toolbarAddTask') }}
        </Button>
      </template>
    </Grid>
  </Page>
</template>
