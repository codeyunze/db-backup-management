import type { RouteRecordRaw } from 'vue-router';

import { $t } from '#/locales';

const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'mdi:database-cog-outline',
      order: 1100,
      title: $t('page.backup.management'),
    },
    name: 'Backup',
    path: '/backup',
    children: [
      {
        name: 'DbInstanceManagement',
        path: '/backup/db-instance',
        component: () => import('#/views/backup/db-instance/index.vue'),
        meta: {
          icon: 'mdi:database-settings-outline',
          title: $t('page.backup.dbInstance'),
        },
      },
      // {
      //   name: 'DataBackup',
      //   path: '/backup/data-backup',
      //   component: () => import('#/views/backup/data-backup/index.vue'),
      //   meta: {
      //     icon: 'mdi:database-arrow-down-outline',
      //     title: '数据备份',
      //   },
      // },
      // {
      //   name: 'DataRestore',
      //   path: '/backup/data-restore',
      //   component: () => import('#/views/backup/data-restore/index.vue'),
      //   meta: {
      //     icon: 'mdi:database-arrow-up-outline',
      //     title: '数据还原',
      //   },
      // },
      {
        name: 'BackupFiles',
        path: '/backup/backup-files',
        component: () => import('#/views/backup/backup-files/index.vue'),
        meta: {
          icon: 'mdi:folder-file-outline',
          title: $t('page.backup.backupFiles'),
        },
      },
      {
        name: 'BackupJobSchedule',
        path: '/backup/job-schedule',
        component: () => import('#/views/backup/job-schedule/index.vue'),
        meta: {
          icon: 'mdi:calendar-clock-outline',
          title: $t('page.backup.jobSchedule'),
        },
      },
    ],
  },
];

export default routes;
