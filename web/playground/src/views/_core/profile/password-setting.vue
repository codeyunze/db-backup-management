<script setup lang="ts">
import type { VbenFormSchema } from '#/adapter/form';

import { computed } from 'vue';

import { ProfilePasswordSetting, z } from '@vben/common-ui';

import { message } from 'ant-design-vue';

import { changePasswordApi, getLoginRsaKeyApi } from '#/api/core/auth';
import { rsaOaepEncryptBase64 } from '#/utils/rsa-oaep';

const formSchema = computed((): VbenFormSchema[] => {
  return [
    {
      fieldName: 'oldPassword',
      label: '旧密码',
      component: 'VbenInputPassword',
      componentProps: {
        placeholder: '请输入旧密码',
      },
    },
    {
      fieldName: 'newPassword',
      label: '新密码',
      component: 'VbenInputPassword',
      componentProps: {
        passwordStrength: true,
        placeholder: '请输入新密码',
      },
    },
    {
      fieldName: 'confirmPassword',
      label: '确认密码',
      component: 'VbenInputPassword',
      componentProps: {
        passwordStrength: true,
        placeholder: '请再次输入新密码',
      },
      dependencies: {
        rules(values) {
          const { newPassword } = values;
          return z
            .string({ required_error: '请再次输入新密码' })
            .min(1, { message: '请再次输入新密码' })
            .refine((value) => value === newPassword, {
              message: '两次输入的密码不一致',
            });
        },
        triggerFields: ['newPassword'],
      },
    },
  ];
});

async function handleSubmit(values: Record<string, any>) {
  const oldPlain = String(values.oldPassword || '');
  const newPlain = String(values.newPassword || '');

  const rsaOld = await getLoginRsaKeyApi();
  const encryptedOldPassword = await rsaOaepEncryptBase64(
    rsaOld.publicKey,
    oldPlain,
  );

  const rsaNew = await getLoginRsaKeyApi();
  const encryptedNewPassword = await rsaOaepEncryptBase64(
    rsaNew.publicKey,
    newPlain,
  );

  await changePasswordApi({
    encryptedOldPassword,
    oldKeyId: rsaOld.keyId,
    encryptedNewPassword,
    newKeyId: rsaNew.keyId,
  });

  message.success('密码修改成功');
}
</script>
<template>
  <ProfilePasswordSetting
    class="w-1/3"
    :form-schema="formSchema"
    @submit="handleSubmit"
  />
</template>
