import { createRequire } from 'node:module'
import { join } from 'node:path'
import process from 'node:process'
import { defineConfig } from 'vitepress'

// docs/ 位于 vite root 之外, 编译 md 页面生成的 SSR 渲染函数会
// import 'vue/server-renderer', VitePress 内置 alias 只覆盖 'vue' 主入口,
// 子路径需自行指到 website/node_modules 下的实际文件
const pkgRequire = createRequire(join(process.cwd(), 'package.json'))
// vue 主入口解析到 ESM 运行时构建 (require 条件解析到 CJS, 不适合浏览器)
const vueRuntime = pkgRequire.resolve('vue/dist/vue.runtime.esm-bundler.js')
const vueServerRenderer = pkgRequire.resolve('vue/server-renderer')

// 文档复用仓库内 docs/ 目录（README 中的相对链接不受影响），
// 首页 landing 位于 docs/index.md，静态资源在 docs/public/（含 CNAME）。
export default defineConfig({
  lang: 'zh-CN',
  title: 'MiAir Next',
  description:
    '让小米小爱音箱化身 DLNA 渲染器、AirPlay 接收器与 Spotify Connect 设备，附带现代化 Web 管理后台，一条命令部署。',
  // 站点绑定自定义子域名 (docs/public/CNAME), 子域名即站点根
  base: '/',
  srcDir: '../docs',
  // publicDir 采用默认约定: srcDir/public (即 docs/public/)
  // srcDir 在网站目录之外, 而 Vite 默认 root = srcDir, 会导致
  // md 页面编译时无法解析 website/node_modules 下的依赖; root 指回 website
  // 后, vite 的 publicDir 也随 root 解析, 需要显式指回 docs/public
  vite: {
    root: '.',
    publicDir: '../docs/public',
    resolve: {
      alias: [
        { find: /^vue\/server-renderer$/, replacement: vueServerRenderer },
        { find: /^vue$/, replacement: vueRuntime }
      ]
    }
  },
  ignoreDeadLinks: [
    // 仓库根目录相对链接（README 引用）在站点内不存在, 不校验
    /^\/(CHANGELOG|LICENSE|NOTICE|img|README)/
  ],
  head: [['link', { rel: 'icon', type: 'image/png', href: '/favicon.png' }]],
  themeConfig: {
    logo: '/favicon.png',
    nav: [
      { text: '首页', link: '/' },
      { text: '快速开始', link: '/quickstart' },
      {
        text: '文档',
        items: [
          { text: '功能特性', link: '/features' },
          { text: '快速开始', link: '/quickstart' },
          { text: '部署与配置', link: '/deployment' },
          { text: 'AirPlay 2（实验性）', link: '/deployment-airplay2' },
          { text: 'Spotify 畅联指南', link: '/spotify' },
          { text: '接口文档', link: '/api' },
          { text: '依赖组件', link: '/dependencies' },
          { text: '本地开发', link: '/development' }
        ]
      }
    ],
    sidebar: [
      {
        text: '开始',
        items: [
          { text: '功能特性', link: '/features' },
          { text: '快速开始', link: '/quickstart' }
        ]
      },
      {
        text: '部署',
        items: [
          { text: '部署与配置', link: '/deployment' },
          { text: 'AirPlay 2（实验性）', link: '/deployment-airplay2' },
          { text: 'Spotify 畅联指南', link: '/spotify' }
        ]
      },
      {
        text: '参考',
        items: [
          { text: '接口文档', link: '/api' },
          { text: '依赖组件', link: '/dependencies' }
        ]
      },
      {
        text: '开发',
        items: [{ text: '本地开发', link: '/development' }]
      }
    ],
    socialLinks: [{ icon: 'github', link: 'https://github.com/deerwan/miair-next' }],
    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
          modal: {
            noResultsText: '未找到相关结果',
            resetButtonTitle: '清除查询条件',
            displayDetails: '显示详细列表',
            hideDetails: '隐藏详细列表',
            footer: { selectText: '选择', navigateText: '切换', closeText: '关闭' }
          }
        }
      }
    },
    outline: { level: [2, 3], label: '本页目录' },
    lastUpdated: { text: '最后更新于' },
    docFooter: { prev: '上一页', next: '下一页' },
    returnToTopLabel: '回到顶部',
    sidebarMenuLabel: '菜单',
    darkModeSwitchLabel: '主题',
    lightModeSwitchTitle: '切换到亮色模式',
    darkModeSwitchTitle: '切换到暗色模式',
    editLink: {
      pattern: 'https://github.com/deerwan/miair-next/edit/main/docs/:path',
      text: '在 GitHub 上编辑此页'
    },
    footer: {
      message: '基于 GPL-3.0-or-later 发布 · 协议引擎源自 MiAir',
      copyright: 'Copyright © 2024-2026 deerwan'
    }
  }
})
