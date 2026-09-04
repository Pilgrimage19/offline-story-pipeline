# 协作与每日同步规范

## 每天开始

在开始修改前，先同步远端主分支并确认工作区状态：

```powershell
git pull --ff-only origin main
git status --short --branch
```

如果本地有未提交改动，先检查是否属于本人上次工作；不要覆盖或重置这些改动。远端有新提交且无法 fast-forward 时，暂停并人工处理冲突。

## 每天结束

结束当天工作前：

1. 运行与本次改动相关的测试（至少 `python -m pytest -q`）。
2. 执行 `git diff --check`。
3. 只提交正式代码、测试和设计文档；不要提交 API Key、`.env`、临时探针、模型缓存或未经确认的生成产物。
4. 使用清晰的提交说明提交全部正式改动。
5. 推送当天提交：

```powershell
git push origin main
```

6. 确认 `git status --short --branch` 显示本地分支与 `origin/main` 同步，并在交接说明中记录提交号、测试结果和未提交文件。

## 数据与大文件

- 原文、运行产物和向量索引是否上传，必须单独确认；不要因为 `.gitignore` 被绕过就自动强制添加。
- `.env` 和任何凭据永远不提交。
- 临时文件保留本地即可，交接时说明其存在，不纳入每日同步。
