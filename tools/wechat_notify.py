# -*- coding: utf-8 -*-
"""通过 ZCode 已连接的微信 iLink 机器人给用户手机推送一条文本消息。

用法: python tools/wechat_notify.py "消息内容" [--dry-run]
凭据读取并解密自 ~/.zcode/v2（与 ZCode 客户端同一台机器、同一用户），仅在内存中使用。
"""
import base64
import hashlib
import json
import os
import random
import sys
import urllib.request

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("需要 cryptography 库: pip install cryptography", file=sys.stderr)
    sys.exit(2)

ZCODE_V2 = os.path.join(os.path.expanduser("~"), ".zcode", "v2")
BASE = "https://ilinkai.weixin.qq.com/ilink/bot"


def decrypt_credential(value: str) -> str:
    """按 ZCode enc:v1 方案解密：sha256(平台:homedir:用户名) 为 key 的 AES-256-GCM。"""
    secret = os.environ.get("ZCODE_CREDENTIAL_SECRET")
    if not secret:
        node_platform = {"nt": "win32"}.get(os.name, sys.platform)
        import getpass
        secret = f"zcode-credential-fallback:{node_platform}:{os.path.expanduser('~')}:{getpass.getuser()}"
    key = hashlib.sha256(secret.encode()).digest()
    iv_s, tag_s, ct_s = value[len("enc:v1:"):].split(".")
    b64u = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    plain = AESGCM(key).decrypt(b64u(iv_s), b64u(ct_s) + b64u(tag_s), None)
    return plain.decode("utf-8")


def load_context() -> tuple[str, str, str]:
    bots = json.load(open(os.path.join(ZCODE_V2, "bot-config.v3.json"), encoding="utf-8"))["bots"]
    bot = next(b for b in bots if b.get("provider") == "weixin" and b.get("enabled"))
    state = json.load(open(os.path.join(ZCODE_V2, "bot-state.v3.json"), encoding="utf-8"))
    st = state["bots"][bot["id"]]
    to_user = st.get("privateRecipientId", "")
    if not to_user:
        raise SystemExit("bot-state 中没有 privateRecipientId，请先用微信给机器人发一条消息建立会话")
    creds = json.load(open(os.path.join(ZCODE_V2, "credentials.json"), encoding="utf-8"))
    token = decrypt_credential(creds[bot["credentialRef"]])
    return bot["providerUserId"], to_user, token


def send(text: str) -> dict:
    from_user, to_user, token = load_context()
    import uuid
    body = {
        "base_info": {"channel_version": "2.0.0"},
        "msg": {
            "from_user_id": from_user,
            "to_user_id": to_user,
            "client_id": f"zcode-weixin-{uuid.uuid4()}",
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text.replace("\r\n", "\n").replace("\n", "\r\n")}}],
        },
    }
    req = urllib.request.Request(
        f"{BASE}/sendmessage",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": base64.b64encode(str(random.randint(0, 2**32)).encode()).decode(),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    ret = payload.get("ret", payload.get("errcode"))
    if ret not in (0, None):
        raise SystemExit(f"发送失败: {json.dumps(payload, ensure_ascii=False)[:300]}")
    return payload


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    msg = sys.argv[1]
    if "--dry-run" in sys.argv:
        print("dry-run:", msg[:200])
    else:
        send(msg)
        print("sent OK")
