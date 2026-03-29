import { addCollection } from '@iconify/vue';

/**
 * 将常用 Iconify 集合打包进应用，菜单/路由 meta.icon 不再请求 api.iconify.design。
 * （网络不可达时 Icon 会渲染空 SVG，表现为「有占位无图形」。）
 */
export async function setupIconifyCollections(): Promise<void> {
  const [
    { default: mdi },
    { default: lucide },
    { default: logos },
    { default: carbon },
    { default: ic },
    { default: ion },
    { default: charm },
  ] = await Promise.all([
    import('@iconify/json/json/mdi.json'),
    import('@iconify/json/json/lucide.json'),
    import('@iconify/json/json/logos.json'),
    import('@iconify/json/json/carbon.json'),
    import('@iconify/json/json/ic.json'),
    import('@iconify/json/json/ion.json'),
    import('@iconify/json/json/charm.json'),
  ]);

  addCollection(mdi as never);
  addCollection(lucide as never);
  addCollection(logos as never);
  addCollection(carbon as never);
  addCollection(ic as never);
  addCollection(ion as never);
  addCollection(charm as never);
}
