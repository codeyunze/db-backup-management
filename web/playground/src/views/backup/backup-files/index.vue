<script lang="ts" setup>
import type { CSSProperties } from 'vue';

import type { VbenFormProps } from '#/adapter/form';
import type { VxeTableGridOptions } from '#/adapter/vxe-table';
import type { BackupFilesApi } from '#/api/backup/backup-files';

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue';

import { Page, useVbenDrawer } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import {
  Button,
  Drawer,
  message,
  Modal,
  Radio,
  Spin,
  Table,
  Tabs,
} from 'ant-design-vue';

import { useVbenVxeGrid } from '#/adapter/vxe-table';
import {
  deleteBackupFile,
  downloadBackupArchive,
  getBackupFileLogs,
  getBackupFilesList,
  getBackupFileTables,
} from '#/api/backup/backup-files';
import { $t } from '#/locales';
import DataBackupForm from '#/views/backup/components/data-backup-form.vue';
import DataRestoreForm from '#/views/backup/components/data-restore-form.vue';

type RowType = BackupFilesApi.BackupFile;

const allBackupFilesRaw = ref<RowType[]>([]);

const backupFormRef = ref<any>();
const [BackupDataDrawer, backupDrawerApi] = useVbenDrawer({
  onCancel() {
    backupDrawerApi.close();
  },
  onConfirm: async () => {
    backupDrawerApi.lock();
    try {
      await backupFormRef.value?.executeBackup?.();
    } finally {
      backupDrawerApi.unlock();
    }
  },
  onOpenChange(isOpen) {
    if (!isOpen) return;
    backupFormRef.value?.resetExecuteLock?.();
    backupFormRef.value?.resetIncrementPresetLock?.();
  },
  placement: 'right',
  confirmText: $t('page.backup.backupFilesPage.dataBackupForm.executeBackup'),
  cancelText: $t('common.cancel'),
});

const restoreFormRef = ref<any>();
const [RestoreDataDrawer, restoreDrawerApi] = useVbenDrawer({
  onCancel() {
    restoreDrawerApi.close();
  },
  onConfirm: async () => {
    restoreDrawerApi.lock();
    try {
      await restoreFormRef.value?.executeRestore?.();
    } finally {
      restoreDrawerApi.unlock();
    }
  },
  onOpenChange(isOpen) {
    if (!isOpen) {
      restoreInitial.value = null;
      return;
    }
    restoreFormRef.value?.resetExecuteLock?.();
  },
  placement: 'right',
  confirmText: $t('page.backup.backupFilesPage.dataRestoreForm.executeRestore'),
  cancelText: $t('common.cancel'),
});

const restoreInitial = ref<null | { database: string; dirName: string }>(null);

const tablesDrawerOpen = ref(false);
const tablesLoading = ref(false);
const tablesDetail = ref<BackupFilesApi.BackupTablesDetail | null>(null);

const logsDrawerOpen = ref(false);
const logsLoading = ref(false);
const logsDetail = ref<BackupFilesApi.BackupLogsDetail | null>(null);
/** 日志抽屉内标签：备份日志 | 还原日志 */
const logsTabKey = ref<'backup' | 'restore'>('backup');
const backupLogPanelRef = ref<HTMLElement>();
const restoreLogPanelRef = ref<HTMLElement>();
const backupLogsLayoutRef = ref<HTMLElement>();
const logPanelHeight = ref(360);
/** 日志抽屉主布局区：底边距浏览器窗口底部留白（px） */
const LOG_LAYOUT_VIEWPORT_BOTTOM_GAP_PX = 50;
const logsLayoutMaxHeightPx = ref<null | number>(null);
const incrementDrawerOpen = ref(false);
const incrementFullRecord = ref<null | RowType>(null);
const incrementRows = ref<RowType[]>([]);

const activeLogPanelRef = computed(() =>
  logsTabKey.value === 'backup'
    ? backupLogPanelRef.value
    : restoreLogPanelRef.value,
);

/** 仅限制最大高度，短日志随内容增高，避免出现大块空白 */
const backupLogPanelStyle = computed(() => ({
  maxHeight: `${logPanelHeight.value}px`,
}));

const backupLogsLayoutStyle = computed<CSSProperties>(() =>
  logsLayoutMaxHeightPx.value === null
    ? {}
    : {
        /* 仅 max-height 时布局会随内容收缩，日志区变矮；固定 height 才能吃满「距窗口底 50px」的可用高度 */
        height: `${logsLayoutMaxHeightPx.value}px`,
        maxHeight: `${logsLayoutMaxHeightPx.value}px`,
        minHeight: 0,
        overflow: 'hidden',
        boxSizing: 'border-box',
      },
);

function raf(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => resolve());
  });
}

/** 日志区与 tabs 内容区底边的内边距（避免贴死） */
const LOG_PANEL_HOLDER_GAP_PX = 8;

async function updateBackupLogsLayoutMaxHeight() {
  const layout = backupLogsLayoutRef.value;
  if (!layout || !logsDrawerOpen.value) return;
  const { top } = layout.getBoundingClientRect();
  logsLayoutMaxHeightPx.value = Math.max(
    200,
    Math.floor(window.innerHeight - top - LOG_LAYOUT_VIEWPORT_BOTTOM_GAP_PX),
  );
}

async function updateLogPanelHeight() {
  if (!logsDrawerOpen.value) return;
  await nextTick();
  await raf();
  await updateBackupLogsLayoutMaxHeight();
  await nextTick();
  await raf();
  const el = activeLogPanelRef.value;
  if (!el) return;
  // 按「Tabs 内容区」实际高度计算，自动为底部「刷新」栏留出空间，避免 ant-drawer-body 被撑出滚动条
  const holder = el.closest('.ant-tabs-content-holder');
  if (holder) {
    const h = holder.getBoundingClientRect();
    const p = el.getBoundingClientRect();
    const byHolder = Math.floor(h.bottom - p.top - LOG_PANEL_HOLDER_GAP_PX);
    logPanelHeight.value = Math.max(120, byHolder);
    return;
  }
  const { top } = el.getBoundingClientRect();
  logPanelHeight.value = Math.max(
    180,
    Math.floor(window.innerHeight - top - LOG_LAYOUT_VIEWPORT_BOTTOM_GAP_PX),
  );
}

const logsBackupDisplay = computed(() => {
  const d = logsDetail.value;
  if (!d) return '';
  return d.backupLogPresent
    ? d.backupLog || $t('page.backup.backupFilesPage.emptyFile')
    : $t('page.backup.backupFilesPage.noBackupLogs');
});

const logsRestoreDisplay = computed(() => {
  const d = logsDetail.value;
  if (!d) return '';
  return d.restoreLogPresent
    ? d.restoreLog || $t('page.backup.backupFilesPage.emptyFile')
    : $t('page.backup.backupFilesPage.noRestoreLogs');
});
const tablesTab = ref<'all' | 'table' | 'view'>('all');

const tablesDisplayRows = computed(() => {
  const d = tablesDetail.value;
  if (!d) return [];
  if (tablesTab.value === 'table') return d.tables;
  if (tablesTab.value === 'view') return d.views;
  return d.items;
});

/** 受控分页，否则 showSizeChanger 修改 pageSize 不生效（静态 pagination 对象会固定 pageSize） */
const tablesPage = ref(1);
const tablesPageSize = ref(15);

watch(tablesTab, () => {
  tablesPage.value = 1;
});

watch(tablesDetail, () => {
  tablesPage.value = 1;
});

watch(tablesDisplayRows, (rows) => {
  const total = rows.length;
  if (total === 0) return;
  const maxPage = Math.max(1, Math.ceil(total / tablesPageSize.value));
  if (tablesPage.value > maxPage) {
    tablesPage.value = maxPage;
  }
});

const tablesPagination = computed(() => ({
  current: tablesPage.value,
  pageSize: tablesPageSize.value,
  total: tablesDisplayRows.value.length,
  showSizeChanger: true,
  pageSizeOptions: ['10', '15', '20', '50', '100'],
  showTotal: (t: number) => $t('page.backup.backupFilesPage.showTotal', [t]),
  onChange: (page: number, size?: number) => {
    tablesPage.value = page;
    if (size !== undefined) tablesPageSize.value = size;
  },
  onShowSizeChange: (_current: number, size: number) => {
    tablesPage.value = 1;
    tablesPageSize.value = size;
  },
}));

const objectTableColumns = [
  {
    title: $t('page.backup.backupFilesPage.type'),
    dataIndex: 'kind',
    key: 'kind',
    width: 72,
  },
  {
    title: $t('page.backup.backupFilesPage.schema'),
    dataIndex: 'schema',
    key: 'schema',
    width: 100,
  },
  {
    title: $t('page.backup.backupFilesPage.name'),
    dataIndex: 'name',
    key: 'name',
    ellipsis: true,
  },
  {
    title: $t('page.backup.backupFilesPage.rows'),
    dataIndex: 'rows',
    key: 'rows',
    width: 96,
  },
];

function objectTableRowKey(record: BackupFilesApi.BackupObjectItem) {
  return `${record.schema}.${record.name}`;
}

function formatBytes(n: number) {
  if (n === undefined || n === null || Number.isNaN(Number(n))) return '-';
  const v = Number(n);
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(2)} KB`;
  return `${(v / 1024 / 1024).toFixed(2)} MB`;
}

const formOptions: VbenFormProps = {
  collapsed: false,
  schema: [
    {
      component: 'Input',
      fieldName: 'keyword',
      label: $t('page.backup.backupFilesPage.keyword'),
      componentProps: {
        placeholder: $t('page.backup.backupFilesPage.keywordPlaceholder'),
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
      handleDelete(row);
      break;
    }
    case 'download': {
      void handleDownload(row);
      break;
    }
    case 'logs': {
      void handleOpenLogs(row);
      break;
    }
    case 'restore': {
      openRestoreDrawer(row);
      break;
    }
    case 'tables': {
      void handleOpenTables(row);
      break;
    }
    case 'viewIncrement': {
      handleOpenIncrements(row);
      break;
    }
    default: {
      break;
    }
  }
}

function hasIncrementChildren(row: RowType) {
  const fullId = (row.backup_file_id || '').trim();
  if (!fullId) return false;
  return allBackupFilesRaw.value.some(
    (x) =>
      (x.backup_type || 'full') === 'increment' &&
      (x.full_backup_file_id || '').trim() === fullId,
  );
}

function handleOpenIncrements(row: RowType) {
  const fullId = (row.backup_file_id || '').trim();
  if (!fullId) return;
  incrementFullRecord.value = row;
  incrementRows.value = allBackupFilesRaw.value
    .filter(
      (x) =>
        (x.backup_type || 'full') === 'increment' &&
        (x.full_backup_file_id || '').trim() === fullId,
    )
    .sort((a, b) => (b.backupTime || '')?.localeCompare(a.backupTime || ''));
  incrementDrawerOpen.value = true;
}

async function startIncrementBackupFromFull() {
  const full = incrementFullRecord.value;
  if (!full) return;
  incrementDrawerOpen.value = false;
  backupDrawerApi.open();
  await nextTick();
  await backupFormRef.value?.reloadOptions?.();
  await backupFormRef.value?.prepareIncrementBackupFromFull?.(full);
}

async function handleOpenLogs(row: RowType) {
  if (!row?.dirName) return;
  logsDetail.value = null;
  logsTabKey.value = 'backup';
  logsDrawerOpen.value = true;
  logsLoading.value = true;
  try {
    logsDetail.value = await getBackupFileLogs(row.dirName);
  } catch {
    message.error($t('page.backup.backupFilesPage.loadLogsFailed'));
    logsDrawerOpen.value = false;
  } finally {
    logsLoading.value = false;
    void updateLogPanelHeight();
  }
}

async function openLogsByDirName(dirName: string) {
  const dn = (dirName || '').trim();
  if (!dn) return;
  await handleOpenLogs({ dirName: dn } as RowType);
}

async function handleRefreshLogs() {
  const currentDir = logsDetail.value?.dirName;
  if (!currentDir) return;
  logsLoading.value = true;
  try {
    logsDetail.value = await getBackupFileLogs(currentDir);
    await updateLogPanelHeight();
    message.success($t('page.backup.backupFilesPage.refreshLogsSuccess'));
  } catch {
    message.error($t('page.backup.backupFilesPage.refreshLogsFailed'));
  } finally {
    logsLoading.value = false;
  }
}

async function handleOpenTables(row: RowType) {
  tablesDetail.value = null;
  tablesTab.value = 'all';
  tablesPage.value = 1;
  tablesPageSize.value = 15;
  tablesDrawerOpen.value = true;
  tablesLoading.value = true;
  try {
    tablesDetail.value = await getBackupFileTables(row.dirName);
  } catch {
    message.error($t('page.backup.backupFilesPage.loadBackupObjectsFailed'));
    tablesDrawerOpen.value = false;
  } finally {
    tablesLoading.value = false;
  }
}

async function handleDownload(row: RowType) {
  try {
    const blob = await downloadBackupArchive(row.dirName);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${row.dirName}_backup.tar.gz`;
    a.click();
    URL.revokeObjectURL(url);
    message.success($t('page.backup.backupFilesPage.downloadStarted'));
  } catch {
    message.error($t('page.backup.backupFilesPage.downloadFailed'));
  }
}

async function onBackupDataSuccess(payload?: { dirName?: string }) {
  gridApi.reload();
  const dirName = (payload?.dirName || '').trim();
  if (!dirName) return;
  backupDrawerApi.close();
  await nextTick();
  await openLogsByDirName(dirName);
}

function openRestoreDrawer(row: RowType) {
  restoreInitial.value = {
    dirName: row.dirName,
    database: row.database,
  };
  restoreDrawerApi.open();
}

function onRestoreSuccess() {
  gridApi.reload();
}

function handleDelete(row: RowType) {
  Modal.confirm({
    title: $t('page.backup.backupFilesPage.confirmDeleteTitle'),
    content: $t('page.backup.backupFilesPage.confirmDeleteContent', [
      row.dirName,
    ]),
    async onOk() {
      await deleteBackupFile(row.dirName);
      message.success($t('page.backup.backupFilesPage.deleteSuccess'));
      gridApi.reload();
    },
  });
}

const gridOptions: VxeTableGridOptions<RowType> = {
  columns: [
    { title: $t('page.backup.backupFilesPage.seq'), type: 'seq', width: 60 },
    {
      field: 'database',
      title: $t('page.backup.backupFilesPage.databaseName'),
      minWidth: 120,
    },
    {
      field: 'dirName',
      title: $t('page.backup.backupFilesPage.dirName'),
      minWidth: 200,
    },
    {
      field: 'backupTime',
      title: $t('page.backup.backupFilesPage.backupTime'),
      minWidth: 180,
    },
    {
      field: 'size',
      title: $t('page.backup.backupFilesPage.fileSize'),
      width: 120,
      formatter: ({ cellValue }) => formatBytes(cellValue as number),
    },
    {
      align: 'right',
      field: 'operation',
      fixed: 'right',
      headerAlign: 'center',
      showOverflow: false,
      title: $t('page.backup.backupFilesPage.operations'),
      width: 400,
      cellRender: {
        attrs: {
          nameField: 'dirName',
          onClick: onActionClick,
        },
        name: 'CellOperation',
        options: [
          {
            code: 'tables',
            text: $t('page.backup.backupFilesPage.actionTables'),
          },
          {
            code: 'viewIncrement',
            show: (r: RowType) => hasIncrementChildren(r),
            text: $t('page.backup.backupFilesPage.actionViewIncrement'),
          },
          { code: 'logs', text: $t('page.backup.backupFilesPage.actionLogs') },
          {
            code: 'download',
            text: $t('page.backup.backupFilesPage.actionDownload'),
          },
          {
            code: 'restore',
            text: $t('page.backup.backupFilesPage.actionRestore'),
          },
          'delete',
        ],
      },
    },
  ],
  height: 'auto',
  keepSource: true,
  pagerConfig: {},
  rowConfig: {
    keyField: 'dirName',
  },
  proxyConfig: {
    ajax: {
      query: async ({ page }, formValues) => {
        const items = await getBackupFilesList(formValues?.keyword);
        allBackupFilesRaw.value = items;
        const displayItems = items.filter(
          (x) => (x.backup_type || 'full') === 'full',
        );
        return {
          items: displayItems,
          total: displayItems.length,
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

watch(logsDrawerOpen, (open) => {
  if (!open) {
    logsLayoutMaxHeightPx.value = null;
  }
});

watch([logsDrawerOpen, logsTabKey, logsLoading, logsDetail], async ([open]) => {
  if (!open) return;
  await updateLogPanelHeight();
});

onMounted(() => {
  gridApi.query();
  window.addEventListener('resize', updateLogPanelHeight);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateLogPanelHeight);
});
</script>

<template>
  <Page auto-content-height>
    <BackupDataDrawer
      class="backup-data-drawer w-full max-w-[800px]"
      :title="$t('page.backup.backupFilesPage.drawerBackupDataTitle')"
    >
      <DataBackupForm
        ref="backupFormRef"
        hide-footer
        @success="onBackupDataSuccess"
      />
      <template #append-footer>
        <Button
          :loading="backupFormRef?.testConnectionLoading"
          @click="backupFormRef?.testConnection"
        >
          {{ $t('page.backup.backupFilesPage.dataBackupForm.testConnection') }}
        </Button>
      </template>
    </BackupDataDrawer>

    <RestoreDataDrawer
      class="restore-data-drawer w-full max-w-[800px]"
      :title="$t('page.backup.backupFilesPage.drawerRestoreDataTitle')"
    >
      <DataRestoreForm
        v-if="restoreInitial"
        ref="restoreFormRef"
        :key="restoreInitial.dirName"
        hide-footer
        :initial-database="restoreInitial.database"
        :initial-dir-name="restoreInitial.dirName"
        @success="onRestoreSuccess"
      />
      <template #append-footer>
        <Button
          :loading="restoreFormRef?.testConnectionLoading"
          @click="restoreFormRef?.testConnection"
        >
          {{ $t('page.backup.backupFilesPage.dataRestoreForm.testConnection') }}
        </Button>
      </template>
    </RestoreDataDrawer>

    <Drawer
      v-model:open="tablesDrawerOpen"
      placement="right"
      :title="$t('page.backup.backupFilesPage.drawerTablesTitle')"
      :width="720"
      destroy-on-close
    >
      <Spin :spinning="tablesLoading">
        <div
          v-if="tablesDetail"
          class="mb-3 text-sm leading-relaxed text-gray-500"
        >
          <div>
            <span class="font-medium text-gray-900">{{
              $t('page.backup.backupFilesPage.directory')
            }}</span>
            ：{{ tablesDetail.dirName }}
          </div>
          <div>
            <span class="font-medium text-gray-900">{{
              $t('page.backup.backupFilesPage.database')
            }}</span>
            ：{{ tablesDetail.database }}
          </div>
          <div>
            {{ $t('page.backup.backupFilesPage.total') }}
            <span class="font-medium text-gray-900">{{
              tablesDetail.table_count
            }}</span>
            {{ $t('page.backup.backupFilesPage.tablesCountSuffix') }}
            <span class="font-medium text-gray-900">{{
              tablesDetail.view_count
            }}</span>
            {{ $t('page.backup.backupFilesPage.viewsCountSuffix') }} ({{
              $t('page.backup.backupFilesPage.sourceLabel')
            }}:
            {{
              tablesDetail.itemsSource === 'filenames'
                ? $t('page.backup.backupFilesPage.sourceFromFilenames')
                : $t('page.backup.backupFilesPage.sourceFromMydumper')
            }})
          </div>
        </div>
        <Radio.Group
          v-model:value="tablesTab"
          button-style="solid"
          class="mb-2"
          option-type="button"
        >
          <Radio.Button value="all">
            {{ $t('page.backup.backupFilesPage.tabAll') }} ({{
              tablesDetail?.items.length ?? 0
            }})
          </Radio.Button>
          <Radio.Button value="table">
            {{ $t('page.backup.backupFilesPage.tabTable') }} ({{
              tablesDetail?.table_count ?? 0
            }})
          </Radio.Button>
          <Radio.Button value="view">
            {{ $t('page.backup.backupFilesPage.tabView') }} ({{
              tablesDetail?.view_count ?? 0
            }})
          </Radio.Button>
        </Radio.Group>
        <Table
          :columns="objectTableColumns"
          :data-source="tablesDisplayRows"
          :pagination="tablesPagination"
          :row-key="objectTableRowKey"
          size="small"
          :scroll="{ y: 420 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'kind'">
              <span>{{
                record.kind === 'view'
                  ? $t('page.backup.backupFilesPage.tablesKindView')
                  : $t('page.backup.backupFilesPage.tablesKindTable')
              }}</span>
            </template>
          </template>
        </Table>
      </Spin>
    </Drawer>

    <Drawer
      v-model:open="incrementDrawerOpen"
      placement="right"
      :title="$t('page.backup.backupFilesPage.drawerIncrementTitle')"
      :width="760"
      destroy-on-close
    >
      <div
        v-if="incrementFullRecord"
        class="mb-3 flex w-full flex-row items-center justify-between border-t p-2 px-3 text-sm leading-relaxed text-gray-500"
      >
        <div class="min-w-0">
          <span class="font-medium text-gray-900">{{
            $t('page.backup.backupFilesPage.directory')
          }}</span>
          ：{{ incrementFullRecord.dirName }}
        </div>
        <Button type="primary" @click="startIncrementBackupFromFull">
          {{ $t('page.backup.backupFilesPage.startIncrementBackup') }}
        </Button>
      </div>
      <Table
        :columns="[
          {
            title: $t('page.backup.backupFilesPage.seq'),
            key: 'seq',
            customRender: ({ index }: any) => index + 1,
            width: 60,
          },
          {
            title: $t('page.backup.backupFilesPage.dirName'),
            dataIndex: 'dirName',
            key: 'dirName',
            ellipsis: true,
          },
          {
            title: $t('page.backup.backupFilesPage.backupTime'),
            dataIndex: 'backupTime',
            key: 'backupTime',
            width: 180,
          },
          {
            title: $t('page.backup.backupFilesPage.fileSize'),
            key: 'size',
            width: 120,
            customRender: ({ record }: any) => formatBytes(record.size),
          },
          // 增量回放信息暂不在列表展示（可在调试模式下再开启）
        ]"
        :data-source="incrementRows"
        :pagination="{ pageSize: 10, showSizeChanger: true }"
        row-key="backup_file_id"
        size="small"
      />
    </Drawer>

    <Drawer
      v-model:open="logsDrawerOpen"
      class="backup-logs-drawer"
      placement="right"
      :title="$t('page.backup.backupFilesPage.drawerLogsTitle')"
      :width="800"
      destroy-on-close
    >
      <Spin :spinning="logsLoading" class="backup-logs-spin">
        <div
          v-if="logsDetail"
          ref="backupLogsLayoutRef"
          class="backup-logs-layout"
          :style="backupLogsLayoutStyle"
        >
          <p class="backup-logs-meta text-sm text-gray-500">
            <span class="font-medium text-gray-900">{{
              $t('page.backup.backupFilesPage.directory')
            }}</span>
            ：{{ logsDetail.dirName }}
          </p>
          <Tabs
            v-model:active-key="logsTabKey"
            class="backup-logs-tabs"
            destroy-inactive-tab-pane
          >
            <Tabs.TabPane key="backup">
              <template #tab>
                <span
                  class="backup-logs-tab-label inline-flex items-center gap-1.5"
                >
                  <IconifyIcon
                    class="size-4 shrink-0"
                    icon="mdi:database-export-outline"
                  />
                  {{ $t('page.backup.backupFilesPage.backupLogTab') }}
                </span>
              </template>
              <div
                ref="backupLogPanelRef"
                class="backup-log-panel"
                :style="backupLogPanelStyle"
              >
                {{ logsBackupDisplay }}
              </div>
            </Tabs.TabPane>
            <Tabs.TabPane key="restore">
              <template #tab>
                <span
                  class="backup-logs-tab-label inline-flex items-center gap-1.5"
                >
                  <IconifyIcon
                    class="size-4 shrink-0"
                    icon="mdi:database-import-outline"
                  />
                  {{ $t('page.backup.backupFilesPage.restoreLogTab') }}
                </span>
              </template>
              <div
                ref="restoreLogPanelRef"
                class="backup-log-panel"
                :style="backupLogPanelStyle"
              >
                {{ logsRestoreDisplay }}
              </div>
            </Tabs.TabPane>
          </Tabs>
        </div>
        <div
          class="mt-auto flex w-full flex-row items-center justify-end gap-x-2 border-t p-2 px-3"
        >
          <Button
            :loading="logsLoading"
            :disabled="!logsDetail"
            type="primary"
            @click="handleRefreshLogs"
          >
            {{ $t('page.backup.backupFilesPage.refresh') }}
          </Button>
        </div>
      </Spin>
    </Drawer>

    <Grid :table-title="$t('page.backup.backupFilesPage.gridTableTitle')">
      <template #toolbar-tools>
        <Button type="primary" @click="backupDrawerApi.open()">
          {{ $t('page.backup.backupFilesPage.toolbarBackupData') }}
        </Button>
      </template>
    </Grid>
  </Page>
</template>

<style scoped>
/* 抽屉内纵向占满，仅日志面板滚动（避免整块 drawer-body 被内容撑出滚动条） */
.backup-data-drawer :deep(.ant-drawer-content-wrapper) {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.restore-data-drawer :deep(.ant-drawer-content-wrapper) {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.backup-data-drawer :deep(.ant-drawer-wrapper-body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.restore-data-drawer :deep(.ant-drawer-wrapper-body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.backup-data-drawer :deep(.ant-drawer-body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: hsl(var(--background)) !important;
}

.restore-data-drawer :deep(.ant-drawer-body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: hsl(var(--background)) !important;
}

.backup-logs-drawer :deep(.ant-drawer-content-wrapper) {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.backup-logs-drawer :deep(.ant-drawer-content) {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  max-height: 100%;
  overflow: hidden;
}

.backup-logs-drawer :deep(.ant-drawer-header) {
  flex-shrink: 0;
}

.backup-logs-drawer :deep(.ant-drawer-wrapper-body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* 覆盖 antd 默认，避免整块 body 随长日志出现滚动条 */
.backup-logs-drawer :deep(.ant-drawer-body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  max-height: 100%;
  padding: 0 24px;
  overflow: hidden !important;
}

.backup-logs-spin {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.backup-logs-spin :deep(.ant-spin-nested-loading) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.backup-logs-spin :deep(.ant-spin-container) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* 外层：目录一行固定高度 + Tabs 区域占满剩余（绝不跟日志一起滚） */
.backup-logs-layout {
  display: grid;
  flex: 1;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 0;
  min-height: 0;
  margin-top: 12px;
  overflow: hidden;
}

.backup-logs-meta {
  align-self: start;
  margin: 0;
  margin-bottom: 12px;
}

/* Tabs 根即 ant-tabs：用网格把「导航栏」与「内容区」拆开，只有内容区参与纵向伸缩 */
.backup-logs-tabs {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  grid-template-columns: minmax(0, 1fr);
  align-self: stretch;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

/* Tabs：经典顶栏线 + 下划线指示条（参考 Ant Design 线型 Tab） */
.backup-logs-tabs :deep(.ant-tabs-nav) {
  grid-row: 1;
  align-self: start;
  padding: 0;
  margin: 0;
  background: transparent;
}

.backup-logs-tabs :deep(.ant-tabs-nav::before) {
  border-bottom: 1px solid hsl(var(--border));
}

.backup-logs-tabs :deep(.ant-tabs-tab) {
  padding: 12px 0 14px !important;
  margin: 0 28px 0 0 !important;
  background: transparent !important;
  border: none !important;
  border-radius: 0;
  transition: color 0.2s ease;
}

.backup-logs-tabs :deep(.ant-tabs-tab):last-child {
  margin-right: 0 !important;
}

.backup-logs-tabs :deep(.ant-tabs-tab-btn) {
  font-weight: 400;
  color: hsl(var(--foreground) / 75%);
  text-shadow: none;
}

.backup-logs-tabs :deep(.ant-tabs-tab-active) {
  background: transparent !important;
  box-shadow: none;
}

.backup-logs-tabs :deep(.ant-tabs-tab-active .ant-tabs-tab-btn) {
  font-weight: 500;
  color: hsl(var(--primary));
}

.backup-logs-tabs :deep(.ant-tabs-ink-bar) {
  height: 2px;
  background: hsl(var(--primary));
  border-radius: 1px;
}

.backup-logs-tabs :deep(.ant-tabs-content-holder) {
  display: flex;
  flex-direction: column;
  grid-row: 2;
  min-height: 0;
  margin-top: 0;
  overflow: hidden;
  background: hsl(var(--muted) / 20%);
  border: none;
  border-radius: 0;
}

.backup-logs-tabs :deep(.ant-tabs-content) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* 勿对全部 tabpane 写 display:flex !important，会盖住 antd 非激活态的 display:none，造成隐藏页仍占位/空白 */
.backup-logs-tabs :deep(.ant-tabs-tabpane.ant-tabs-tabpane-active) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.backup-log-panel {
  box-sizing: border-box;
  flex: 0 1 auto;
  align-self: stretch;
  width: 100%;
  min-width: 0;
  min-height: 0;
  padding: 12px 14px;
  overflow: auto;
  overscroll-behavior: contain;
  font-family:
    ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono',
    'Courier New', monospace;
  font-size: 12px;
  line-height: 1.55;
  color: hsl(var(--foreground));
  word-break: break-word;
  white-space: pre-wrap;
  background: transparent;
  -webkit-overflow-scrolling: touch;
}
</style>
