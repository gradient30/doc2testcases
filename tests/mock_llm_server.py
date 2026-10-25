# -*- coding: utf-8 -*-
"""本地 mock 模型端点（OpenAI 兼容），用于全链路机械验收（不依赖真实 LLM）。

按提示词内容区分应答：
  - 评审提示（含「测试用例集（JSON）」）-> 返回 approved=true 的评审结论
  - 其他（生成/修订）                    -> 返回固定的小型合法用例集
启动: python tests/mock_llm_server.py [port]   默认 8790
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

CASESET = {
    "document": None,  # 运行时按请求里的文档名填充
    "module": "订单计价与优惠规则",
    "notes": "mock 假设：R04 运费门槛按优惠后金额判断",
    "cases": [
        {"id": "TC-01", "title": "订单金额恰为199元命中满减R01（减30）", "priority": "P0",
         "type": "功能测试", "preconditions": "结算环境可用", "steps": ["1. 下单199元", "2. 查看优惠明细"],
         "expected": "命中R01减30元，应付169元", "source": "R01"},
        {"id": "TC-02", "title": "订单金额199元与99元满减互斥取最优", "priority": "P1",
         "type": "规则测试", "preconditions": "无", "steps": ["1. 下单199元"],
         "expected": "只应用R01，不叠加R02", "source": "R01/R02"},
        {"id": "TC-03", "title": "订单金额88元整免运费边界", "priority": "P2",
         "type": "边界测试", "preconditions": "无", "steps": ["1. 下单88元"],
         "expected": "运费0元", "source": "R03"},
    ],
}
REVIEW = {"approved": True, "findings": []}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        user = body["messages"][-1]["content"]
        if "测试用例集（JSON）" in user:
            payload = REVIEW
        else:
            cs = json.loads(json.dumps(CASESET))
            m = re.search(r"源文档：(.+?)（", user)
            if m:
                cs["document"] = m.group(1)
            payload = cs
        out = {"choices": [{"message": {"content": "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"}}]}
        data = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 静默
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    print(f"mock llm on http://127.0.0.1:{port}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
