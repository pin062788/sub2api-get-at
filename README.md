# sub2api 401 账号自动补号使用指南

`refresh_sub2api_401.py` 用于扫描 `sub2api` 里状态异常的 OpenAI OAuth 账号，重新走 ChatGPT 邮箱验证码登录，生成新的 OAuth 凭据，再写回或新建到 `sub2api`。

## 功能流程

1. 登录或复用已登录的 `sub2api` 管理端 token。
2. 扫描 `platform=openai&type=oauth` 的账号。
3. 找出错误信息中包含 `401` 或 `unauthorized` 的账号。
4. 从账号凭据、邮箱字段或账号名称中提取邮箱。
5. 通过 ChatGPT 邮箱验证码登录。
6. 从 Cloudflare Temp Email 或本地 Hotmail Helper 获取验证码。
7. 获取 ChatGPT session/token 并转换成 `sub2api` OAuth 凭据。
8. 默认更新原账号，也可以直接创建新账号绕过旧错误状态。

## 安装依赖

建议使用虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

依赖目前只有：

```text
curl_cffi
```

## 快速命令

公网 `sub2api` 只扫描 401：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python refresh_sub2api_401.py \
  --sub-base-url https://sub2api.example.com \
  --sub-access-token 'Bearer <SUB2API_ACCESS_TOKEN>' \
  --sub-group '<OPENAI_GROUP_NAME>' \
  --dry-run --save-queue
```

公网 `sub2api` 扫描并修复 401。先启动 Hotmail Helper：

```bash
python3 scripts/hotmail_helper.py --port 17373
```

准备 `hotmail_accounts.txt`：

```text
email----password----clientId----refreshToken
```

再执行修复：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --sub-base-url https://sub2api.example.com \
  --sub-access-token 'Bearer <SUB2API_ACCESS_TOKEN>' \
  --sub-group '<OPENAI_GROUP_NAME>' \
  --otp-provider hotmail-local \
  --hotmail-helper-url http://127.0.0.1:17373 \
  --hotmail-accounts-file hotmail_accounts.txt \
  --create-instead-of-update \
  --skip-if-active-email-exists \
  --otp-timeout 300 \
  --otp-interval 8
```

`--create-instead-of-update` 会直接创建新账号，适合旧 401 账号状态无法自动清除的环境；`--skip-if-active-email-exists` 用来避免同邮箱重复创建。

## 扫描 401

本地 `sub2api` 可直接用账号密码：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --sub-base-url http://localhost:8080 \
  --sub-admin-email '<SUB2API_ADMIN_EMAIL>' \
  --sub-admin-password '<SUB2API_ADMIN_PASSWORD>' \
  --sub-group '<OPENAI_GROUP_NAME>' \
  --dry-run --save-queue
```

公网 `sub2api` 如果开启了 Cloudflare Turnstile，推荐从浏览器已登录后台的 Network 请求里复制 `Authorization: Bearer ...`，然后跳过登录：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --sub-base-url https://sub2api.example.com \
  --sub-access-token 'Bearer <SUB2API_ACCESS_TOKEN>' \
  --sub-group '<OPENAI_GROUP_NAME>' \
  --dry-run --save-queue
```

`--save-queue` 会把扫描到的 401 邮箱写入 `401_accounts.txt`，只用于排查和预检；正式运行仍会实时扫描 `sub2api`。

## Outlook/Hotmail Helper

Outlook/Hotmail 邮箱需要启动本项目自带的 `scripts/hotmail_helper.py`。它用 `clientId + refreshToken` 换 Microsoft access token，优先通过 IMAP XOAUTH2 读取 `outlook.office365.com`，失败时回退到 Graph API 和 Outlook REST API。它不会用 Outlook 密码登录网页版。

先启动 helper：

```bash
cd "$PWD"
python3 scripts/hotmail_helper.py --port 17373
```

保持 helper 终端打开。看到类似输出说明启动成功：

```text
Hotmail helper listening on http://127.0.0.1:17373
```

helper 会在本项目 `data/` 目录下写少量运行记录文件；`data/` 默认不建议提交。

账号文件格式：

```text
email----password----clientId----refreshToken
```

保存为 `hotmail_accounts.txt`。脚本会用 401 账号邮箱在该文件中精确匹配对应的 `clientId/refreshToken`，几百个账号也可以直接放进去。

可以用下面的命令检查 helper 是否能读邮箱：

```bash
.venv/bin/python - <<'PY'
from refresh_sub2api_401 import load_hotmail_accounts
from curl_cffi import requests as rq

accounts = load_hotmail_accounts("hotmail_accounts.txt")
email = next(iter(accounts))
account = accounts[email]
response = rq.post("http://127.0.0.1:17373/messages", json={
    "email": email,
    "clientId": account["client_id"],
    "refreshToken": account["refresh_token"],
    "mailboxes": ["INBOX", "Junk"],
    "top": 2,
}, timeout=60)
print(response.status_code)
print(response.text[:1000])
PY
```

正常返回应为 JSON，包含 `ok: true` 和 `messages`。

## 正式运行

默认策略是更新原 401 账号：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --sub-base-url https://sub2api.example.com \
  --sub-access-token 'Bearer <SUB2API_ACCESS_TOKEN>' \
  --sub-group '<OPENAI_GROUP_NAME>' \
  --otp-provider hotmail-local \
  --hotmail-helper-url http://127.0.0.1:17373 \
  --hotmail-accounts-file hotmail_accounts.txt \
  --limit 1
```

如果旧账号更新成功但 `sub2api` 仍残留 `status=error` 或旧 `error_message`，可以改用直接创建新账号：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --sub-base-url https://sub2api.example.com \
  --sub-access-token 'Bearer <SUB2API_ACCESS_TOKEN>' \
  --sub-group '<OPENAI_GROUP_NAME>' \
  --otp-provider hotmail-local \
  --hotmail-helper-url http://127.0.0.1:17373 \
  --hotmail-accounts-file hotmail_accounts.txt \
  --create-instead-of-update \
  --skip-if-active-email-exists \
  --otp-timeout 300 \
  --otp-interval 8
```

`--create-instead-of-update` 会直接 `POST /api/v1/admin/accounts` 创建新账号，不更新旧 401 账号。

`--skip-if-active-email-exists` 会在同邮箱已有 `active` 账号时跳过旧 401，避免重复创建。

## Cloudflare Temp Email

如果账号邮箱是 Cloudflare Temp Email，使用默认验证码来源即可：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --otp-provider temp-email \
  --temp-api 'https://temp-email.example.com' \
  --temp-admin-auth '<TEMP_EMAIL_ADMIN_AUTH>' \
  --temp-domain '<TEMP_EMAIL_DOMAIN>'
```

如果 Worker 有额外访问密码：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --temp-custom-auth '你的CustomAuth'
```

如果收信端点不是默认路径：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --temp-mail-paths '/admin/mails,/api/messages'
```

脚本默认会尝试：

```text
/admin/mails
/admin/messages
/admin/mail
/api/mails
/api/messages
/mails
/messages
```

## 常用参数

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `--sub-base-url` | `http://localhost:8080` | `sub2api` 管理端基础地址 |
| `--sub-admin-email` | 占位值 | `sub2api` 管理员邮箱 |
| `--sub-admin-password` | 占位值 | `sub2api` 管理员密码 |
| `--sub-turnstile-token` | 空 | 公网登录开启 Turnstile 时使用的登录验证码 token |
| `--sub-access-token` | 空 | 已登录后台 API 请求里的 `Authorization: Bearer ...`，提供后跳过登录 |
| `--sub-group` | 占位值 | 写入账号时使用的 OpenAI 分组名 |
| `--otp-provider` | `temp-email` | 验证码来源：`temp-email` 或 `hotmail-local` |
| `--hotmail-helper-url` | `http://127.0.0.1:17373` | 本地 Hotmail Helper 地址 |
| `--hotmail-accounts-file` | 空 | Hotmail 账号文件 |
| `--hotmail-account` | 空 | 单条 Hotmail 账号，格式同账号文件 |
| `--hotmail-mailboxes` | `INBOX,Junk` | Hotmail helper 查询的邮箱目录 |
| `--temp-api` | 占位值 | Cloudflare Temp Email API 地址 |
| `--temp-admin-auth` | 占位值 | Cloudflare Temp Email Admin Auth |
| `--temp-custom-auth` | 空 | Cloudflare Worker 额外访问认证 |
| `--temp-domain` | 占位值 | 临时邮箱域名 |
| `--temp-mail-paths` | 自动尝试常见路径 | 自定义收信接口路径 |
| `--queue-file` | `401_accounts.txt` | 保存 401 邮箱队列的文件名 |
| `--save-queue` | 关闭 | 额外保存扫描到的邮箱队列 |
| `--dry-run` | 关闭 | 只扫描，不登录 ChatGPT、不写入 |
| `--limit` | `0` | 限制处理数量，`0` 表示不限制 |
| `--proxy` | 空 | 访问 ChatGPT 使用的代理 |
| `--otp-timeout` | `180` | 等待验证码的超时时间，单位秒 |
| `--otp-interval` | `5` | 轮询邮箱验证码的间隔，单位秒 |
| `--create-instead-of-update` | 关闭 | 直接创建新账号，不更新原 401 账号 |
| `--skip-if-active-email-exists` | 关闭 | 同邮箱已有 active 账号时跳过旧 401 |
| `--create-on-update-fail` | 关闭 | PUT 更新失败时改为创建新账号 |

## 常见问题

### 公网登录提示 turnstile verification failed

说明公网后台开启了 Turnstile。优先使用浏览器已登录后台请求里的 `Authorization: Bearer ...`，通过 `--sub-access-token` 跳过登录。

### Hotmail helper 返回 502 或空响应

通常不是 helper 正常响应。先确认 helper 真正在监听：

```bash
python3 scripts/hotmail_helper.py --port 17373
```

正常 helper 的 `/messages` 和 `/code` 返回 JSON。若原生 socket 连接被拒绝，说明 helper 没启动或端口不对。

### 验证码校验失败

脚本已经拿到验证码，但 OpenAI 校验不接受。常见原因是验证码邮件延迟、重复旧码、账号风控或 OpenAI 登录流程状态异常。可以单账号重试并拉长等待：

```bash
.venv/bin/python refresh_sub2api_401.py \
  --limit 1 \
  --otp-timeout 300 \
  --otp-interval 8
```

### 更新成功但旧账号仍然 401

部分 `sub2api` 环境 PUT 成功后不会清掉旧账号的 `status/error_message`，或写入后立刻被上游检测为异常。此时可以用：

```bash
--create-instead-of-update --skip-if-active-email-exists
```

先创建新 active 账号，再后续手动处理旧 401 账号。

## 安全注意

- `sub2api` Bearer token、管理员密码、Hotmail `refreshToken`、Temp Email Admin Auth 都是敏感凭据。
- 不要提交 `hotmail_accounts.txt`、`401_accounts.txt`、`.venv/`、缓存文件。
- `--save-queue` 生成的 `401_accounts.txt` 会包含邮箱地址，用完可删除。
