# 提交 MiAir Next 到 iStoreOS 软件中心

iStoreOS 软件中心里**所有 Docker 类应用**（Jellyfin / Navidrome / Home Assistant 等）都遵循同一模式：
一个轻量 `luci-app-xxx` 插件负责 `docker run` 拉起镜像，再配一个 `app-meta-xxx` 元数据包做商店展示。

本目录已准备好全部物料：

```
istore/
├── luci-app-miair-next/      # OpenWRT 插件（docker 拉起 + 软件中心页面）
│   ├── Makefile
│   ├── luasrc/controller/miair-next.lua
│   ├── luasrc/model/cbi/miair-next/{status,script}.lua
│   ├── luasrc/view/miair-next/miair-next.htm
│   ├── po/zh-cn/miair-next.po
│   ├── root/etc/config/miair-next
│   └── root/usr/share/miair-next/install.sh
├── app-meta-miair-next/      # 商店元数据包
│   ├── Makefile
│   └── logo.png
├── README-istore.md          # 商店展示用的教程文案
└── SUBMIT.md                 # 本文件
```

---

## 上架路径（推荐：提 PR）

iStoreOS 的插件与元数据分别收录在两个仓库，需要各提一个 PR：

### 1. 编译 `luci-app-miair-next` 并上传 ipk → `istore-repo`

iStoreOS 不收插件源码，只收**编译好的 ipk**。需先在 OpenWRT 编译环境里把
`luci-app-miair-next` 编出 ipk，再上传到 `linkease/istore-repo`（ipk 仓库）。

#### 1a. 编译 ipk（OpenWRT 编译环境）

```bash
# 在 OpenWRT 源码树 feeds 中加入本插件
mkdir -p package/luci-app-miair-next
cp -r /path/to/miair-next/istore/luci-app-miair-next/* package/luci-app-miair-next/
./scripts/feeds update -a && ./scripts/feeds install -a
make menuconfig        # 勾选 LuCI -> Applications -> luci-app-miair-next
make package/luci-app-miair-next/compile V=s
# 产物：bin/packages/<架构>/luci/luci-app-miair-next_1.0.0-1_all.ipk
# 翻译包：bin/packages/<架构>/luci/luci-i18n-miair-next-zh-cn_*.ipk
```

> 需同时编译翻译包 `luci-i18n-miair-next-zh-cn`（来自 `po/zh-cn/miair-next.po`），
> 否则 `META_DEPENDS` 里的 `+luci-i18n-miair-next-zh-cn` 会找不到。

#### 1b. 上传 ipk 到 istore-repo

```bash
git clone https://github.com/<你>/istore-repo
cd istore-repo
# istore-repo 只收 aarch64 和 x86_64 两类顶层架构目录（cortex-a53/generic 均归到 aarch64/ 下），文件名须带版本号
cp /path/to/luci-app-miair-next_1.0.0-1_all.ipk bin/packages/aarch64/
cp /path/to/luci-i18n-miair-next-zh-cn_*.ipk   bin/packages/aarch64/
git add bin/packages/aarch64
git commit -m "feat: add luci-app-miair-next ipk"
git push

# 在 GitHub 上向 linkease/istore-repo 的 pending 分支提 PR
```

> 注：`istore-repo` 只收已编译的 ipk/apk，**不收源码**；`istoreos/istore-packages`
> 不是官方收录目标。PR 须提向 `pending` 分支，main 为保护分支不可直推。

### 2. 提 `app-meta-miair-next` → 元数据仓库

收录在 `linkease/openwrt-app-meta`（已验证的真实仓库）。

```bash
git clone https://github.com/<你>/openwrt-app-meta
cd openwrt-app-meta
mkdir -p applications/app-meta-miair-next
cp -r /path/to/miair-next/istore/app-meta-miair-next/* applications/app-meta-miair-next/
git add applications/app-meta-miair-next
git commit -m "feat: add app-meta-miair-next"
git push

# 在 GitHub 上向 linkease/openwrt-app-meta 提 PR
```

### 3. 等待审核

官方合并后，软件中心刷新即可看到 **MiAir Next**，分类在「网络 / 多媒体」。

---

## 备用路径：邮件投稿

若不想维护 PR，可走官方投稿通道，由官方把应用收录进商店：

- 收件人：`admin@linkease.com`
- 主题：`报名参加iStore共创教程活动 - MiAir Next - <你的账号名>`

邮件正文模板：

```
软件名称：MiAir Next
教程链接：https://github.com/deerwan/miair-next/blob/main/istore/README-istore.md
账号信息：
  - 媒体平台：GitHub
  - 账号名称：deerwan
  - 邮箱：<你的邮箱>
  - 个人主页：https://github.com/deerwan/miair-next

应用简介：
  MiAir Next 让小米小爱音箱化身 DLNA / AirPlay 接收器，附 Web 管理后台。
  以 Docker 容器运行（host 网络，支持 amd64/arm64），已提供 luci-app-miair-next
  插件与 app-meta-miair-next 元数据包，可直接收录。
```

---

## 验证清单（提交前自查）

- [ ] `luci-app-miair-next/Makefile` 中 `LUCI_DEPENDS:=+docker` 已声明 Docker 依赖
- [ ] `install.sh` 使用 `--network host`（DLNA/AirPlay 组播必需）
- [ ] 镜像 `mrdeer1997/miair-next:latest` 已发布且为多架构
- [x] `app-meta-miair-next/logo.png` 为 256x256 PNG（已就绪）
- [ ] `META_DEPENDS` 指向 `+luci-app-miair-next +luci-i18n-miair-next-zh-cn +docker-deps`
- [ ] `META_LUCI_ENTRY` 与插件 controller 的 entry 路径一致
