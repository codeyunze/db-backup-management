<script setup lang="ts">
import type { AboutProps, DescriptionItem } from './about';

import { h } from 'vue';

import {
  VBEN_DOC_URL,
  VBEN_GITHUB_URL,
  // VBEN_PREVIEW_URL,
} from '@vben/constants';

import { VbenRenderContent } from '@vben-core/shadcn-ui';

import { Page } from '../../components';

defineOptions({
  name: 'AboutUI',
});

defineProps<AboutProps>();

declare global {
  const __VBEN_ADMIN_METADATA__: {
    authorEmail: string;
    authorName: string;
    authorUrl: string;
    buildTime: string;
    dependencies: Record<string, string>;
    description: string;
    devDependencies: Record<string, string>;
    homepage: string;
    license: string;
    repositoryUrl: string;
    version: string;
  };
}

const renderLink = (href: string, text: string) =>
  h(
    'a',
    { href, target: '_blank', class: 'vben-link' },
    { default: () => text },
  );

const {
  // authorEmail,
  // authorName,
  authorUrl,
  buildTime,
  // homepage,
  license,
  version,
  // vite inject-metadata 插件注入的全局变量
} = __VBEN_ADMIN_METADATA__ || {};

const backupToolVersion = (
  import.meta.env.VITE_BACKUP_TOOL_VERSION || '26.2.0'
).trim();

const toolAuthor = (import.meta.env.VITE_BACKUP_TOOL_AUTHOR || '云泽').trim();

const mydumperVersion = (
  import.meta.env.VITE_BACKUP_MYDUMPER_VERSION || 'v0.21.3-2'
).trim();

const toolAuthorEmail = (
  import.meta.env.VITE_BACKUP_TOOL_AUTHOR_EMAIL || '834363368@qq.com'
).trim();

const vbenDescriptionItems: DescriptionItem[] = [
  {
    content: backupToolVersion,
    title: '备份工具版本',
  },
  {
    content: license,
    title: '开源许可协议',
  },
  {
    content: buildTime,
    title: '最后构建时间',
  },
  // {
  //   content: renderLink(homepage, '点击查看'),
  //   title: '主页',
  // },
  {
    content: renderLink(VBEN_DOC_URL, '点击查看'),
    title: '文档地址',
  },
  // {
  //   content: renderLink(VBEN_PREVIEW_URL, '点击查看'),
  //   title: '预览地址',
  // },
  {
    // content: renderLink(VBEN_GITHUB_URL, '点击查看'),
    content: h('div', [
      renderLink(`${VBEN_GITHUB_URL}`, 'db-backup-management'),
    ]),
    title: 'Github',
  },
  // {
  //   content: h('div', [
  //     renderLink(authorUrl, `${authorName}  `),
  //     renderLink(`mailto:${authorEmail}`, authorEmail),
  //   ]),
  //   title: '作者',
  // },
  {
    content: h('div', [
      renderLink(authorUrl, `${toolAuthor}  `),
      renderLink(`mailto:${toolAuthorEmail}`, toolAuthorEmail),
    ]),
    title: '作者',
  },
];

/** 备份 Docker 镜像（AlmaLinux 9 + dnf 分支）典型版本，可通过 VITE_BACKUP_* 覆盖 */
const frontendVbenVersion = (
  import.meta.env.VITE_FRONTEND_VBEN_VERSION ||
  version ||
  ''
).trim();

const backupRuntimeDeps: DescriptionItem[] = [
  {
    content: frontendVbenVersion,
    title: 'Vben Admin',
  },
  {
    content: mydumperVersion,
    title: 'mydumper',
  },
  {
    content: (import.meta.env.VITE_BACKUP_PYTHON_VERSION || '3.9.25').trim(),
    title: 'Python',
  },
  {
    content: (import.meta.env.VITE_BACKUP_PIP_VERSION || '21.3.1').trim(),
    title: 'python3-pip',
  },
  {
    content: (
      import.meta.env.VITE_BACKUP_MYSQL_CLIENT_VERSION || '8.4.8'
    ).trim(),
    title: 'MySQL 客户端',
  },
  {
    content: (import.meta.env.VITE_BACKUP_CRONIE_VERSION || '1.5.7').trim(),
    title: 'cronie',
  },
  {
    content: (import.meta.env.VITE_BACKUP_FLASK_VERSION || '3.1.3').trim(),
    title: 'Flask',
  },
  {
    content: (import.meta.env.VITE_BACKUP_WERKZEUG_VERSION || '3.1.6').trim(),
    title: 'Werkzeug',
  },
  {
    content: (import.meta.env.VITE_BACKUP_BASH_VERSION || '5.1.8').trim(),
    title: 'bash',
  },
  {
    content: (import.meta.env.VITE_BACKUP_TZDATA_VERSION || '2026a').trim(),
    title: 'tzdata',
  },
];
</script>

<template>
  <Page :title="title">
    <template #description>
      <p class="text-foreground mt-3 text-sm leading-6">
        <a :href="VBEN_GITHUB_URL" class="vben-link" target="_blank">
          {{ name }}
        </a>
        {{ description }}
      </p>
    </template>
    <div class="card-box p-5">
      <div>
        <h5 class="text-foreground text-lg">基本信息</h5>
      </div>
      <div class="mt-4">
        <dl class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
          <template v-for="item in vbenDescriptionItems" :key="item.title">
            <div class="border-border border-t px-4 py-6 sm:col-span-1 sm:px-0">
              <dt class="text-foreground text-sm font-medium leading-6">
                {{ item.title }}
              </dt>
              <dd class="text-foreground mt-1 text-sm leading-6 sm:mt-2">
                <VbenRenderContent :content="item.content" />
              </dd>
            </div>
          </template>
        </dl>
      </div>
    </div>

    <div class="card-box mt-6 p-5">
      <div>
        <h5 class="text-foreground text-lg">环境依赖</h5>
      </div>
      <div class="mt-4">
        <dl class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
          <template v-for="item in backupRuntimeDeps" :key="item.title">
            <div class="border-border border-t px-4 py-3 sm:col-span-1 sm:px-0">
              <dt class="text-foreground text-sm">
                {{ item.title }}
              </dt>
              <dd class="text-foreground/80 mt-1 text-sm sm:mt-2">
                <VbenRenderContent :content="item.content" />
              </dd>
            </div>
          </template>
        </dl>
      </div>
    </div>
  </Page>
</template>
