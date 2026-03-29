<script lang="ts" setup>
import { Page } from '@vben/common-ui';

import { Card } from 'ant-design-vue';

import DataRestoreForm from '#/views/backup/components/data-restore-form.vue';
</script>

<template>
  <Page auto-content-height content-class="flex flex-col gap-4">
    <Card title="还原参数">
      <DataRestoreForm />
    </Card>
  </Page>
</template>
