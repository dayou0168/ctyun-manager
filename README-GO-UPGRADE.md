# Go 版服务器升级

发布包同时包含 Linux AMD64 和 ARM64 主程序。升级脚本会按服务器架构自动选择预编译程序，先构建新镜像，再备份 SQLite 数据和密钥；新服务健康检查失败时会恢复原 systemd 服务。

```bash
tar -xzf ctyun-manager-go-v1.0.0-linux-amd64-arm64.tar.gz
cd ctyun-manager-go-v1.0.0
sudo CTYUN_MANAGER_INSTALL_DIR=/www/wwwroot/ctyun-manager bash upgrade-go-docker.sh
```

正式升级会短暂停止现有 `ctyun-manager` systemd 服务，并在同一端口启动 Go 容器。执行前请确认安装目录；不要把发布包解压到现有 `data` 目录中。

## CTyunOS V4 ARM L2TP

平台的“安装/更新脚本”按钮仍是推荐入口。脚本识别 `aarch64/arm64`；若 CTyunOS 官方仓库没有 `xl2tpd`，会下载固定的官方 `v1.3.20` 源码、校验 SHA-256，仅编译服务必需的 `xl2tpd` 与 `xl2tpd-control`，因此不再依赖 ARM 仓库中常见缺失的 `libpcap-devel`。

客户端默认地址池为 `172.18.0.2-172.18.255.254`。用户配置第 5 列可填写固定地址、CIDR 或起止范围；保存后应点击“保存并应用”，使账号认证和 xl2tpd 地址池同时重建。
