# 生产部署

生产目录固定为 `/opt/pinhaoke`，应用由 `pinhaoke.service` 以 `www-data` 身份只读运行。更新脚本只更新 Git 工作树、Python 虚拟环境和 systemd unit；它不会安装或覆盖 Nginx、Certbot 及证书配置。

## 更新应用

先确认服务器已经安装 Git、Git LFS、Python 3、`venv`、`curl`、`flock`、`sha256sum` 和 systemd。然后以 root 运行：

```bash
sudo bash /opt/pinhaoke/deploy/update.sh
```

脚本使用非阻塞锁，始终解析并部署精确的 `origin/main` 提交。它会在停服前下载并校验 LFS 对象、从目标提交的 `requirements.txt` 构建候选环境并运行 `pip check`。停服后才会切换代码和环境；服务启动失败或任一 API 烟测失败都会打印 journal 并返回失败。

更新完成后可再次检查：

```bash
systemctl status pinhaoke --no-pager
curl --fail --silent http://127.0.0.1:8000/api/health
```

## 手动安装 Nginx 模板

`deploy/nginx.conf` 是 production site 模板，不是完整的 `nginx.conf`。它引用 Certbot 在 `/etc/letsencrypt/` 管理的证书和 TLS 参数；先确认这些文件存在。安装时先备份当前站点，再复制到临时文件并验证：

```bash
sudo cp /etc/nginx/sites-available/pinhaoke /etc/nginx/sites-available/pinhaoke.backup
sudo cp /opt/pinhaoke/deploy/nginx.conf /etc/nginx/sites-available/pinhaoke.candidate
sudo ln -sfn /etc/nginx/sites-available/pinhaoke.candidate /etc/nginx/sites-enabled/pinhaoke
sudo nginx -t
sudo systemctl reload nginx
```

只有 `nginx -t` 成功后才能 reload。若验证失败，立即把 `sites-enabled/pinhaoke` 指回原文件并再次运行 `nginx -t`。不要让 `deploy/update.sh` 管理这一部分；这样才能与 Certbot 的续期和手工站点调整共存。

## 回滚

更新失败时，脚本会保留旧环境并打印 `.venv-backup-*` 的完整路径。先查看失败日志和当前提交：

```bash
journalctl -u pinhaoke -n 100 --no-pager
git -C /opt/pinhaoke rev-parse HEAD
```

回滚代码时，使用已知正常的完整提交 SHA，不要使用模糊分支名；随后把保留的环境目录与 `/opt/pinhaoke/venv` 在同一文件系统内互换，重新安装该提交的 `deploy/pinhaoke.service`，执行 `systemctl daemon-reload` 并启动服务。确认三个 API 烟测都通过后再删除失败环境和备份。Nginx 回滚独立进行：恢复 `sites-enabled` 的备份目标，运行 `nginx -t`，成功后 reload。
