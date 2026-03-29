<script lang="ts" setup>
import type { VbenFormProps } from '#/adapter/form';
import type { VxeTableGridOptions } from '#/adapter/vxe-table';
import type { DbInstanceApi } from '#/api/backup/db-instance';

import { reactive, ref } from 'vue';

import { Page, useVbenDrawer } from '@vben/common-ui';

import {
  Button,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
} from 'ant-design-vue';

import { useVbenVxeGrid } from '#/adapter/vxe-table';
import {
  createDbInstance,
  deleteDbInstance,
  getDbInstanceList,
  testDbConnection,
  updateDbInstance,
} from '#/api/backup/db-instance';
import { $t } from '#/locales';

type RowType = DbInstanceApi.DbInstance;

const formOptions: VbenFormProps = {
  collapsed: false,
  schema: [
    {
      component: 'Input',
      fieldName: 'keyword',
      label: $t('page.backup.dbInstancePage.keyword'),
      componentProps: {
        placeholder: $t('page.backup.dbInstancePage.keywordPlaceholder'),
      },
    },
  ],
  showCollapseButton: false,
  submitOnChange: true,
  submitOnEnter: true,
};

const gridOptions: VxeTableGridOptions<RowType> = {
  columns: [
    { title: $t('page.backup.dbInstancePage.seq'), type: 'seq', width: 60 },
    {
      field: 'name',
      title: $t('page.backup.dbInstancePage.instanceName'),
      minWidth: 150,
    },
    {
      field: 'host',
      title: $t('page.backup.dbInstancePage.host'),
      minWidth: 140,
    },
    { field: 'port', title: $t('page.backup.dbInstancePage.port'), width: 100 },
    {
      field: 'database',
      title: $t('page.backup.dbInstancePage.databaseName'),
      minWidth: 150,
    },
    {
      field: 'user',
      title: $t('page.backup.dbInstancePage.userName'),
      minWidth: 120,
    },
    {
      slots: { default: 'action' },
      title: $t('page.backup.dbInstancePage.operations'),
      width: 180,
      fixed: 'right',
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
        const items = await getDbInstanceList(formValues?.keyword);
        // 当前接口不做分页，这里把全量当作当前页数据
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

// 这里对参数做一次类型降噪，避免 ts-plugin 泛型过深
const [Grid, gridApi] = useVbenVxeGrid({
  formOptions,
  gridOptions,
} as any);

const editingId = ref('');
const saving = ref(false);
const testingConnection = ref(false);
const formRef = ref();
const model = reactive<Omit<RowType, 'id'> & { id?: string }>({
  id: '',
  name: '',
  host: '127.0.0.1',
  port: 3306,
  user: 'root',
  password: '',
  database: '',
});

const [DbInstanceDrawer, drawerApi] = useVbenDrawer({
  onCancel() {
    drawerApi.close();
  },
  onConfirm: async () => {
    await submit();
  },
  onOpenChange(isOpen) {
    if (!isOpen) return;

    // 强制右侧弹出
    drawerApi.setState({ placement: 'right' });

    const data = drawerApi.getData<any>();
    const isEdit = !!data?.id;

    editingId.value = isEdit ? (data as RowType).id : '';
    model.id = isEdit ? (data as RowType).id : '';
    model.name = isEdit ? (data as RowType).name : '';
    model.host = isEdit ? (data as RowType).host : '127.0.0.1';
    model.port = isEdit ? (data as RowType).port : 3306;
    model.user = isEdit ? (data as RowType).user : 'root';
    model.password = isEdit ? (data as RowType).password : '';
    model.database = isEdit ? (data as RowType).database : '';

    formRef.value?.clearValidate?.();
  },
});

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
      await updateDbInstance(editingId.value, {
        name: model.name,
        host: model.host,
        port: model.port,
        user: model.user,
        password: model.password,
        database: model.database,
      });
      message.success($t('page.backup.dbInstancePage.editSuccess'));
    } else {
      await createDbInstance({
        name: model.name,
        host: model.host,
        port: model.port,
        user: model.user,
        password: model.password,
        database: model.database,
      });
      message.success($t('page.backup.dbInstancePage.createSuccess'));
    }

    gridApi.reload();
    drawerApi.close();
  } finally {
    drawerApi.unlock();
    saving.value = false;
  }
}

function remove(row: any) {
  const r = row as RowType;
  Modal.confirm({
    title: $t('page.backup.dbInstancePage.deleteTitle'),
    content: $t('page.backup.dbInstancePage.deleteContent', [r.name]),
    async onOk() {
      await deleteDbInstance(r.id);
      message.success($t('page.backup.dbInstancePage.deleteSuccess'));
      gridApi.reload();
    },
  });
}

async function testConnection() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  testingConnection.value = true;
  try {
    await testDbConnection({
      host: model.host,
      port: model.port,
      user: model.user,
      password: model.password || '',
      database: model.database,
    });
    message.success($t('page.backup.dbInstancePage.connectSuccess'));
  } catch {
    // 失败提示由 request 拦截器处理
  } finally {
    testingConnection.value = false;
  }
}
</script>

<template>
  <Page auto-content-height>
    <Grid :table-title="$t('page.backup.dbInstancePage.management')">
      <template #toolbar-tools>
        <Button type="primary" @click="openCreate">
          {{ $t('page.backup.dbInstancePage.addInstance') }}
        </Button>
      </template>
      <template #action="{ row }">
        <Button type="link" @click="openEdit(row)">
          {{ $t('page.backup.dbInstancePage.editButton') }}
        </Button>
        <Button danger type="link" @click="remove(row)">
          {{ $t('page.backup.dbInstancePage.deleteButton') }}
        </Button>
      </template>
    </Grid>

    <DbInstanceDrawer
      class="w-full max-w-[800px]"
      :title="
        editingId
          ? $t('page.backup.dbInstancePage.editTitle')
          : $t('page.backup.dbInstancePage.createTitle')
      "
    >
      <Form
        ref="formRef"
        :model="model"
        layout="horizontal"
        :label-col="{ span: 8 }"
        :wrapper-col="{ span: 16 }"
      >
        <template v-if="editingId">
          <div class="grid grid-cols-2 gap-4">
            <Form.Item
              :label="$t('page.backup.dbInstancePage.instanceName')"
              name="name"
              :rules="[
                {
                  required: true,
                  message: $t(
                    'page.backup.dbInstancePage.requiredInstanceName',
                  ),
                },
              ]"
            >
              <Input v-model:value="model.name" />
            </Form.Item>
            <Form.Item
              :label="$t('page.backup.dbInstancePage.databaseName')"
              name="database"
              :rules="[
                {
                  required: true,
                  message: $t(
                    'page.backup.dbInstancePage.requiredDatabaseName',
                  ),
                },
              ]"
            >
              <Input v-model:value="model.database" />
            </Form.Item>

            <Form.Item
              :label="$t('page.backup.dbInstancePage.host')"
              name="host"
              :rules="[
                {
                  required: true,
                  message: $t('page.backup.dbInstancePage.requiredHost'),
                },
              ]"
            >
              <Input v-model:value="model.host" />
            </Form.Item>

            <Form.Item
              :label="$t('page.backup.dbInstancePage.port')"
              name="port"
              :rules="[
                {
                  required: true,
                  message: $t('page.backup.dbInstancePage.requiredPort'),
                },
              ]"
            >
              <InputNumber v-model:value="model.port" :min="1" class="w-full" />
            </Form.Item>

            <Form.Item
              :label="$t('page.backup.dbInstancePage.userName')"
              name="user"
              :rules="[
                {
                  required: true,
                  message: $t('page.backup.dbInstancePage.requiredUserName'),
                },
              ]"
            >
              <Input v-model:value="model.user" />
            </Form.Item>

            <Form.Item
              :label="$t('page.backup.dbInstancePage.password')"
              name="password"
              :rules="[
                {
                  required: true,
                  message: $t('page.backup.dbInstancePage.requiredPassword'),
                },
              ]"
            >
              <Input.Password v-model:value="model.password" />
            </Form.Item>
          </div>
        </template>

        <template v-else>
          <div class="grid grid-cols-2 gap-4">
            <Form.Item
              :label="$t('page.backup.dbInstancePage.instanceName')"
              name="name"
              :rules="[
                {
                  required: true,
                  message: $t(
                    'page.backup.dbInstancePage.requiredInstanceName',
                  ),
                },
              ]"
            >
              <Input v-model:value="model.name" />
            </Form.Item>
            <Form.Item
              :label="$t('page.backup.dbInstancePage.databaseName')"
              name="database"
              :rules="[
                {
                  required: true,
                  message: $t(
                    'page.backup.dbInstancePage.requiredDatabaseName',
                  ),
                },
              ]"
            >
              <Input v-model:value="model.database" />
            </Form.Item>

            <Form.Item
              :label="$t('page.backup.dbInstancePage.host')"
              name="host"
              :rules="[
                {
                  required: true,
                  message: $t('page.backup.dbInstancePage.requiredHost'),
                },
              ]"
            >
              <Input v-model:value="model.host" />
            </Form.Item>

            <Form.Item
              :label="$t('page.backup.dbInstancePage.port')"
              name="port"
              :rules="[
                {
                  required: true,
                  message: $t('page.backup.dbInstancePage.requiredPort'),
                },
              ]"
            >
              <InputNumber v-model:value="model.port" :min="1" class="w-full" />
            </Form.Item>

            <Form.Item
              :label="$t('page.backup.dbInstancePage.userName')"
              name="user"
              :rules="[
                {
                  required: true,
                  message: $t('page.backup.dbInstancePage.requiredUserName'),
                },
              ]"
            >
              <Input v-model:value="model.user" />
            </Form.Item>

            <Form.Item
              :label="$t('page.backup.dbInstancePage.password')"
              name="password"
              :rules="[
                {
                  required: true,
                  message: $t('page.backup.dbInstancePage.requiredPassword'),
                },
              ]"
            >
              <Input.Password v-model:value="model.password" />
            </Form.Item>
          </div>
        </template>
      </Form>

      <template #append-footer>
        <Button :loading="testingConnection" @click="testConnection">
          {{ $t('page.backup.dbInstancePage.testConnection') }}
        </Button>
      </template>
    </DbInstanceDrawer>
  </Page>
</template>
