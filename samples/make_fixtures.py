# -*- coding: utf-8 -*-
"""Create sample requirement fixtures (docx / xlsx / pdf) for the test-case workflow."""
import os

SAMPLES = os.path.dirname(os.path.abspath(__file__))

# ---------- 1. login-requirements.docx ----------
from docx import Document

doc = Document()
doc.add_heading("用户登录模块需求说明书", level=0)
doc.add_heading("1. 账号密码登录", level=1)
doc.add_paragraph("用户输入手机号与密码登录。手机号须为 11 位且已注册；密码为 8-20 位，含字母和数字。"
                  "校验通过则登录成功并跳转首页；任一项错误提示“账号或密码错误”，不区分具体原因。")
doc.add_paragraph("连续输错 5 次密码，账号锁定 15 分钟；锁定期间即使密码正确也提示“账号已锁定，请稍后再试”。")
doc.add_heading("2. 短信验证码登录", level=1)
doc.add_paragraph("输入手机号获取 6 位数字验证码，验证码 5 分钟内有效，同一手机号 60 秒内不可重复获取。"
                  "验证码输错 3 次作废，须重新获取。同一手机号 24 小时内最多发送 10 条。")
doc.add_heading("3. 记住登录状态", level=1)
doc.add_paragraph("登录页勾选“记住我”后，7 天内再次访问免登录；未勾选则关闭浏览器即退出。"
                  "用户主动退出登录后，记住状态失效。")
doc.add_heading("4. 找回密码", level=1)
doc.add_paragraph("通过已绑定的手机号接收验证码重置密码。新密码不得与最近 3 次使用过的密码相同。"
                  "重置成功后所有设备上的登录状态失效，须重新登录。")
table = doc.add_table(rows=4, cols=3)
table.style = "Table Grid"
hdr = table.rows[0].cells
hdr[0].text, hdr[1].text, hdr[2].text = "登录方式", "有效期", "说明"
rows = [("账号密码", "会话期间", "未勾选记住我时"),
        ("短信验证码", "验证码 5 分钟", "每手机号每日 10 条上限"),
        ("记住我", "7 天", "主动退出后失效")]
for i, r in enumerate(rows, start=1):
    c = table.rows[i].cells
    c[0].text, c[1].text, c[2].text = r
doc.save(os.path.join(SAMPLES, "login-requirements.docx"))
print("docx OK")

# ---------- 2. pricing-rules.xlsx ----------
from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws.title = "计价规则"
ws.append(["规则ID", "规则名称", "触发条件", "计算方式", "备注"])
ws.append(["R01", "满减", "订单金额 ≥ 199 元", "减 30 元", "与 R02 不叠加"])
ws.append(["R02", "满减", "订单金额 ≥ 99 元", "减 10 元", "与 R01 不叠加，取最优一条"])
ws.append(["R03", "免运费", "订单金额 ≥ 88 元", "运费 0 元", "以优惠后金额判断"])
ws.append(["R04", "基础运费", "订单金额 < 88 元", "运费 8 元", "—"])
ws.append(["R05", "银卡会员", "会员等级 = 银卡", "订单金额 × 0.95", "与满减叠加，先满减后折扣"])
ws.append(["R06", "金卡会员", "会员等级 = 金卡", "订单金额 × 0.90", "与满减叠加，先满减后折扣"])
ws.append(["R07", "优惠券", "使用单张优惠券", "按券面额抵扣", "每单限 1 张，不与满减叠加，二选一"])
ws.column_dimensions["A"].width = 10
for col in "BCDE":
    ws.column_dimensions[col].width = 26
wb.save(os.path.join(SAMPLES, "pricing-rules.xlsx"))
print("xlsx OK")

# ---------- 3. order-flow.pdf (reportlab, CID Chinese font) ----------
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
style = ParagraphStyle("zh", fontName="STSong-Light", fontSize=10.5, leading=16)
story = [
    Paragraph("订单下单与履约流程需求", ParagraphStyle("h", parent=style, fontSize=16, leading=22)),
    Spacer(1, 4 * mm),
    Paragraph("一、下单流程：购物车结算 → 确认收货地址 → 选择配送方式 → 选择支付方式 → 提交订单。任一步骤中断，已填信息保留 30 分钟。",
              style), Spacer(1, 2 * mm),
    Paragraph("二、配送方式：标准配送（48 小时内发货）；次日达（当日 20:00 前下单且收货地址在支持城市列表内）。次日达运费 12 元，不参与免运费规则。",
              style), Spacer(1, 2 * mm),
    Paragraph("三、支付方式：微信支付、支付宝、货到付款。订单金额 ≥ 500 元不支持货到付款。待支付订单 30 分钟未支付自动取消并释放库存。",
              style), Spacer(1, 2 * mm),
    Paragraph("四、订单状态流转：待支付 → 待发货 → 已发货 → 已签收。已发货 7 天未确认收货则自动确认。自动取消或超时取消的订单进入“已取消”，已支付金额原路退回。",
              style),
]
SimpleDocTemplate(os.path.join(SAMPLES, "order-flow.pdf"), pagesize=A4,
                  topMargin=18 * mm, bottomMargin=18 * mm).build(story)
print("pdf OK")

# ---------- verify: pypdf can extract the Chinese text back ----------
from pypdf import PdfReader
text = "".join(p.extract_text() for p in PdfReader(os.path.join(SAMPLES, "order-flow.pdf")).pages)
print("pdf roundtrip:", "OK" if ("订单" in text and "货到付款" in text) else "GARBLED:", text[:60])

# ---------- verify: xlwt Chinese roundtrip ----------
import xlwt, xlrd, io
book = xlwt.Workbook()
sh = book.add_sheet("用例")
sh.write(0, 0, "用例ID"); sh.write(0, 1, "标题")
sh.write(1, 0, "TC-001"); sh.write(1, 1, "正确账号密码登录成功，跳转首页")
buf = io.BytesIO(); book.save(buf); buf.seek(0)
rb = xlrd.open_workbook(file_contents=buf.read())
got = rb.sheet_by_index(0).cell_value(1, 1)
print("xls roundtrip:", "OK" if got == "正确账号密码登录成功，跳转首页" else f"BAD: {got!r}")

# ---------- verify: minimal XMind ZEN zip ----------
import json, zipfile
content = [{"id": "sheet-1", "class": "sheet", "title": "测试用例",
            "rootTopic": {"id": "root", "class": "topic", "title": "登录模块",
                          "children": {"attached": [
                              {"id": "t1", "class": "topic", "title": "TC-001 正确账密登录",
                               "children": {"attached": [
                                   {"id": "t1a", "class": "topic", "title": "预期：跳转首页"}]}}]}}}]
buf2 = io.BytesIO()
with zipfile.ZipFile(buf2, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("content.json", json.dumps(content, ensure_ascii=False))
    z.writestr("manifest.json", json.dumps({"file-entries": {}}))
    z.writestr("metadata.json", json.dumps({"creator": {"name": "fixture", "version": "1"}}))
with zipfile.ZipFile(buf2) as z:
    parsed = json.loads(z.read("content.json"))
print("xmind structure:", "OK" if parsed[0]["rootTopic"]["title"] == "登录模块" else "BAD")
