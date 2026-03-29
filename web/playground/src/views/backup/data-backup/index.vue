<script lang="ts" setup>
import { Page } from '@vben/common-ui';

import { Card } from 'ant-design-vue';

import DataBackupForm from '../components/data-backup-form.vue';
</script>

<template>
  <Page auto-content-height content-class="flex flex-col gap-4">
    <Card title="备份参数">
      <DataBackupForm />
    </Card>
  </Page>
</template>
