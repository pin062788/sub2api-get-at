# sub2api 401 账号自动补号使用指南

## 1. 功能说明

`refresh_sub2api_401.py` 用于自动处理本地 `sub2api` 中状态异常的 ChatGPT OAuth 账号。

处理流程：

1. 登录 `sub2api` 管理端。
2. 扫描 OpenAI OAuth 账号列表。
3. 找出错误信息中包含 `401` 或 `unauthorized` 的账号。
4. 读取这些账号里的邮箱地址。
5. 用邮箱验证码方式登录 ChatGPT。
6. 通过 Cloudflare Temp Email 自动收取验证码。
7. 获取 `https://chatgpt.com/api/auth/session` 返回的 session/token 信息。
8. 转换成 `sub2api` OpenAI OAuth 账号凭据。
9. 优先更新原账号；如果开启兜底参数，也可以在更新失败时创建新账号。

默认不需要 ChatGPT 密码。

## 2. 当前默认配置

脚本内置了当前环境的默认值：

- `sub2api` 地址：`your real info`
- `sub2api` 管理员邮箱：`your real info`
- `sub2api` 管理员密码：`your real info`
- `sub2api` 分组：`your real info`
- Cloudflare Temp Email API：`your real info`
- Cloudflare Temp Email Admin Auth：`your real info`
- Cloudflare Temp Email 域名：`your real info`

如果这些值没变，正式运行时可以不传参数。

## 3. 安装依赖

进入当前目录：

```bash
cd your real info
```

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

当前依赖只有：

```text
curl_cffi
```

## 4. 先做 dry-run 扫描

建议先执行 dry-run。这个命令只扫描，不登录 ChatGPT，也不会更新 `sub2api`。

```bash
python3 refresh_sub2api_401.py --dry-run
```

如果还想把扫描到的 401 邮箱保存为文本队列：

```bash
python3 refresh_sub2api_401.py --dry-run --save-queue
```

默认队列文件是：

```text
401_accounts.txt
```

队列文件只是辅助查看和排查；正式流程不依赖它。脚本默认会直接从 `sub2api` 扫描后逐个处理。

## 5. 正式运行

默认配置不变时，直接运行：

```bash
python3 refresh_sub2api_401.py
```

限制本次最多处理 1 个账号，适合首次验证：

```bash
python3 refresh_sub2api_401.py --limit 1
```

如果本机访问 ChatGPT 需要代理：

```bash
python3 refresh_sub2api_401.py --proxy your real info
```

也可以使用 socks 代理：

```bash
python3 refresh_sub2api_401.py --proxy your real info
```

## 6. Cloudflare Temp Email 配置

### 6.1 只使用默认 Admin Auth

默认使用占位配置：

```bash
python3 refresh_sub2api_401.py
```

### 6.2 如果还有额外访问密码

如果 Cloudflare Worker 站点还配置了额外访问密码，使用：

```bash
python3 refresh_sub2api_401.py --temp-custom-auth 'your real info'
```

### 6.3 如果收信端点不是默认路径

脚本会自动尝试这些常见收信端点：

```text
/admin/mails
/admin/messages
/admin/mail
/api/mails
/api/messages
/mails
/messages
```

如果你的 Worker 使用了不同路径，可以显式指定，多个路径用英文逗号分隔：

```bash
python3 refresh_sub2api_401.py --temp-mail-paths '/admin/mails,/api/messages'
```

## 7. 常用参数

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `--sub-base-url` | `your real info` | `sub2api` 管理端基础地址 |
| `--sub-admin-email` | `your real info` | `sub2api` 管理员邮箱 |
| `--sub-admin-password` | `your real info` | `sub2api` 管理员密码 |
| `--sub-group` | `your real info` | 更新账号时使用的 OpenAI 分组名 |
| `--temp-api` | `your real info` | Cloudflare Temp Email API 地址 |
| `--temp-admin-auth` | `your real info` | Cloudflare Temp Email Admin Auth |
| `--temp-custom-auth` | 空 | Cloudflare Worker 额外访问认证 |
| `--temp-domain` | `your real info` | 临时邮箱域名 |
| `--temp-mail-paths` | 自动尝试常见路径 | 自定义收信接口路径 |
| `--queue-file` | `401_accounts.txt` | 保存 401 邮箱队列的文件名 |
| `--save-queue` | 关闭 | 额外保存扫描到的邮箱队列 |
| `--dry-run` | 关闭 | 只扫描，不登录、不更新 |
| `--limit` | `0` | 限制处理数量，`0` 表示不限制 |
| `--proxy` | 空 | 访问 ChatGPT 使用的代理 |
| `--otp-timeout` | `180` | 等待验证码的超时时间，单位秒 |
| `--otp-interval` | `5` | 轮询邮箱验证码的间隔，单位秒 |
| `--create-on-update-fail` | 关闭 | 更新原账号失败时改为创建新账号 |

## 8. 推荐运行顺序

第一次使用建议按这个顺序：

```bash
cd your real info
python3 -m pip install -r requirements.txt
python3 refresh_sub2api_401.py --dry-run --save-queue
python3 refresh_sub2api_401.py --limit 1
python3 refresh_sub2api_401.py
```

如果需要代理：

```bash
python3 refresh_sub2api_401.py --limit 1 --proxy your real info
```

确认单个账号成功后，再去掉 `--limit 1` 批量跑。

## 9. 更新策略

脚本默认使用：

```text
PUT /api/v1/admin/accounts/{id}
```

也就是直接更新原来的 401 账号。

这样不依赖“重复导入同邮箱是否覆盖”，也不需要先删除账号。

如果某些环境里更新接口失败，可以开启兜底创建：

```bash
python3 refresh_sub2api_401.py --create-on-update-fail
```

注意：这个参数只在更新失败时创建新账号，不会主动删除旧账号。

## 10. 日志说明

运行时会输出类似日志：

```text
[2026/05/23 13:18:47] INFO: 扫描到 401 账号 3 个
[2026/05/23 13:18:50] INFO: 开始补号：your real info
[2026/05/23 13:18:56] INFO: 等待邮箱验证码：your real info
[2026/05/23 13:19:08] INFO: 已获取邮箱验证码：your real info
[2026/05/23 13:19:15] OK: 补号完成：your real info -> sub2api #123
```

最终会输出成功和失败数量。

## 11. 常见问题

### 11.1 扫描到 0 个 401 账号

说明当前 `sub2api` 账号列表里没有错误信息包含 `401` 或 `unauthorized` 的 OpenAI OAuth 账号。

可以先用：

```bash
python3 refresh_sub2api_401.py --dry-run --save-queue
```

确认队列文件是否为空。

### 11.2 Cloudflare Temp Email 返回 403

通常是认证信息或 Worker 访问保护不匹配。

优先检查：

- `--temp-admin-auth` 是否正确。
- 是否还需要传 `--temp-custom-auth`。
- Worker 的收信端点是否和默认路径一致。

可尝试：

```bash
python3 refresh_sub2api_401.py \
  --temp-custom-auth 'your real info' \
  --temp-mail-paths '/admin/mails,/api/messages'
```

### 11.3 等待验证码超时

可能原因：

- ChatGPT 没有发送验证码。
- Cloudflare Temp Email 没有收到转发邮件。
- 收信端点路径不匹配。
- 验证码邮件延迟。

可以加大等待时间：

```bash
python3 refresh_sub2api_401.py --otp-timeout 300 --otp-interval 5
```

### 11.4 未获取 authorization code

说明验证码后没有顺利进入 OAuth 回调阶段。

常见原因：

- ChatGPT 风控或页面状态变化。
- 代理不稳定。
- 当前账号需要额外验证。

建议先单账号验证：

```bash
python3 refresh_sub2api_401.py --limit 1 --proxy your real info
```

### 11.5 OAuth token 交换失败

说明已拿到授权码，但换 token 阶段失败。

优先检查：

- 网络代理是否稳定。
- ChatGPT 登录流程是否被风控。
- 当前账号是否进入额外验证状态。

## 12. 安全注意事项

- 文档和脚本包含本地 `sub2api` 管理员密码、Temp Email Admin Auth 等配置，建议只放在受控环境中。
- 不要把包含真实管理密码的文件公开提交到公共仓库。
- `--save-queue` 生成的 `401_accounts.txt` 会包含邮箱地址，如不需要可删除。

## 13. 一句话运行版

确认依赖已安装后，默认环境直接运行：

```bash
cd your real info && python3 refresh_sub2api_401.py
```