<div align="center">

# 🖥️ fnmon — fnOS 实时系统监控面板

**一键 Docker 部署 | 6 大监控维度 | 数据可导出**

[![Docker Hub](https://img.shields.io/badge/Docker-Hub-2496ED?style=flat&logo=docker)](https://hub.docker.com/r/jinghui1984/fnmon)
[![GitHub stars](https://img.shields.io/github/stars/personal82555/fnmon?style=social)](https://github.com/personal82555/fnmon)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![fnOS](https://img.shields.io/badge/fnOS-%E9%A3%9E%E7%89%9B-orange)](https://www.fnnas.com)

---

<p align="center">
  <img src="https://img.88531.cn/i/2026/06/15/6a2ed1dd6dbc5.png" alt="fnmon 总览" width="80%"/>
</p>

**fnmon** 是一款专为飞牛 fnOS 打造的实时系统监控面板。一个 Docker 命令即可拥有媲美专业 NAS 监控的 Web 界面，涵盖 **CPU、内存、网络、磁盘、进程排行、应用用量** 六大维度，所有历史数据支持一键导出。

</div>

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🚀 **一键部署** | 一条 `docker run` 命令启动，无需安装驱动 |
| ⚡ **实时推送** | SSE 技术，2 秒刷新一次，延迟极低 |
| 📊 **维度全面** | 6 大面板覆盖系统全部关键指标 |
| 💾 **数据导出** | 一键导出所有历史数据到 NAS 目录，JSON 格式 |
| 📱 **移动适配** | 深色主题，侧边栏导航，屏幕利用率高 |
| 🐳 **零侵入** | Docker 容器运行，不修改系统文件 |

---

## 📸 功能截图

### ⚡ CPU 监控
实时显示 CPU 型号、每个核心的当前频率、Governor/EPP 运行模式、核心温度（支持 SATA/NVMe 硬盘温度）、Turbo Boost 状态、60 秒频率历史曲线图。还包括 CPU 评测跑分（PassMark/Cinebench/Geekbench）和同级 CPU 横向对比。

<p align="center">
  <img src="https://img.88531.cn/i/2026/06/15/6a2ed1db91017.png" alt="CPU 面板" width="90%"/>
</p>

### 🧠 内存监控
总容量 / 已用 / 可用 / 缓存 / SWAP 全面展示，进度条可视化，数据实时更新。

<p align="center">
  <img src="https://img.88531.cn/i/2026/06/15/6a2ed1dcc7cc3.png" alt="内存面板" width="90%"/>
</p>

### 🌐 网络流量
实时上传/下载网速、当月/当日累计流量、Docker 容器流量排行（TOP 15）、60 秒流量趋势图。

<p align="center">
  <img src="https://img.88531.cn/i/2026/06/15/6a2ed1dc13916.png" alt="网络面板" width="90%"/>
</p>

### 💾 磁盘健康
物理硬盘列表（型号、容量、接口类型）、SMART 健康状态、硬盘温度、NVMe 寿命百分比、通电时间、总写入量。磁盘分区挂载点使用率 + 60 秒 I/O 趋势图。

<p align="center">
  <img src="https://img.88531.cn/i/2026/06/15/6a2ed1de2fcce.png" alt="磁盘面板" width="90%"/>
</p>

### 📱 应用用量排行
综合 CPU + 内存 + 网络 三维度加权评分，可按 **日/周/月/年/总计** 切换时间维度，快速定位资源消耗大户。

<p align="center">
  <img src="https://img.88531.cn/i/2026/06/15/6a2ed1df0cf82.png" alt="应用用量排行" width="90%"/>
</p>

### 🔝 进程 CPU 排名
实时 TOP 20 进程，含 PID、用户、CPU%、内存%、启动时间。CPU% 用颜色标识（🔴>50% / 🟠>20% / 🟡>5%）。

---

## 🚀 快速开始

### 方式一：Docker 运行（推荐）

```bash
docker run -d --name fnmon -p 5000:5000 --privileged \
  -v /proc:/proc:ro -v /sys:/sys:ro \
  -v /vol2/fnmon-exports:/var/exports \
  jinghui1984/fnmon
```

启动后浏览器访问 `http://你的NASIP:5000` 即可。

> **📌 导出目录说明：** `-v /vol2/fnmon-exports:/var/exports` 用于保存导出的历史数据，如不需要可省略。

### 方式二：Docker Compose

```yaml
version: '3'
services:
  fnmon:
    image: jinghui1984/fnmon
    container_name: fnmon
    restart: always
    ports:
      - "5000:5000"
    privileged: true
    volumes:
      - /proc:/proc:ro
      - /sys:/sys:ro
      - /vol2/fnmon-exports:/var/exports
    environment:
      - TZ=Asia/Shanghai
```

---

## 🔧 功能详解

### 数据导出

点击侧边栏底部的 **⬇️ 导出** 按钮，一键将所有历史数据保存为 JSON 文件到挂载的 NAS 目录。文件名格式：`fnmon-export-YYYY-MM-DD-HHMMSS.json`。

导出的数据包含：

| 内容 | 说明 |
|------|------|
| CPU 实时数据 | 频率、温度、Governor、EPP |
| 内存快照 | 已用、缓存、SWAP |
| 网络历史 | 60 秒流量队列 |
| 磁盘 I/O | 设备读写速率 |
| 应用用量 | SQLite 全量数据（日/周/月/年 聚合） |
| Docker 流量 | 容器网络存档 |

通过 ⚙️ **设置** 按钮可自由配置导出路径。

### 历史数据追踪

- **网络流量**：累计统计总/日/月 流量，Docker 容器排行
- **应用用量**：60 秒采集一次进程 + Docker 资源快照，自动聚合到日/周/月/年维度
- **SSE 实时推送**：2 秒间隔推送所有数据，前端自动渲染

---

## 🛠️ 技术栈

| 组件 | 技术选型 |
|------|---------|
| 后端框架 | Flask (Python) |
| 前端 | 纯 HTML/CSS/JS（无额外依赖） |
| 实时通信 | Server-Sent Events (SSE) |
| 数据采集 | psutil、smartctl、/proc 文件系统 |
| 数据存储 | SQLite（应用用量 + 流量审计） |
| 容器化 | Docker（基于 python:3.9-slim） |
| 镜像大小 | ~342MB |

---

## 📋 功能清单

- [x] CPU 频率 / 核心温度 / Governor / EPP
- [x] 内存 / SWAP 实时 & 趋势
- [x] 网络流量实时 & 累计（日 / 月 / 总）
- [x] Docker 容器流量排行
- [x] 磁盘分区使用率 & I/O 趋势
- [x] 物理硬盘健康（SMART / NVMe 寿命）
- [x] 进程 CPU 占用排行（TOP 20）
- [x] 应用用量排行（日/周/月/年）
- [x] CPU 评测跑分 & 同级对比
- [x] 一键数据导出
- [ ] 暗色 / 亮色主题切换
- [ ] 微信 / 邮件告警
- [ ] 多语言支持

---

## 📦 下载

| 资源 | 地址 |
|------|------|
| Docker Hub | `jinghui1984/fnmon` |
| GitHub | 本仓库 |
| 论坛讨论 | [飞牛论坛](https://club.fnnas.com) |

---

## 🤝 贡献

欢迎 Issue 和 PR！如果你有好的想法或发现了 Bug，请到 [Issues](https://github.com/personal82555/fnmon/issues) 提出。

---

<div align="center">

**如果这个项目对你有帮助，欢迎 ⭐Star 支持！**

</div>
