# 仓库物料系统

仓库物料流程、库存管理、料卡和数据大屏共用同一个 Flask 服务与 SQLite 数据库。

## 运行环境

- Linux 或 WSL
- Python 3.8 及以上
- 端口默认：`5000`

## 快速启动

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
./run.sh
```

浏览器访问 `http://127.0.0.1:5000`。

后台运行与管理：

```bash
./start_background.sh
./status.sh
./stop.sh
```

## 离线安装

已准备本地依赖包时，执行：

```bash
./install_offline.sh
```

## 离线更新

离线更新功能位于 `offline_update/`，通过根目录入口执行：

```bash
python3 offline_update/interactive.py
```

更新前请备份 `data/` 目录中的数据库文件。

## 主要目录

- `warehouse_suit/`：业务服务、接口、迁移和权限
- `templates/`、`static/`：前端页面和资源
- `tests/`：自动化测试
- `offline_update/`：离线更新工具
- `data/`：本地运行数据，不纳入 Git

## 测试

```bash
python -m pytest
```