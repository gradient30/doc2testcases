/** 单份文档的用例产出摘要（撰写/修订后由撰写员返回）。 */
interface CaseSummary {
  /** 源文档路径 */
  document: string;
  /** 该文档对应的业务模块名，如「用户登录」 */
  module: string;
  /** 用例总数 */
  caseCount: number;
  /** P0（核心功能正向）数量 */
  p0: number;
  /** P1（重要功能与典型异常）数量 */
  p1: number;
  /** P2（边界与次要场景）数量 */
  p2: number;
  /** 需要用户知道的说明：模糊需求、做出的假设等；没有则空字符串 */
  notes: string;
}

/** 一条评审意见。 */
interface ReviewFinding {
  /** 一句话说清缺了什么 / 错在哪 */
  what: string;
  /** high=整个功能点遗漏或预期结果错误；medium=覆盖缺口或步骤不可执行；low=措辞、优先级等小问题 */
  severity: "high" | "medium" | "low";
}

/** 评审员对一份用例集的复查结论。 */
interface ReviewResult {
  /** 用例集是否可直接交付 */
  approved: boolean;
  /** 发现的问题清单，可交付时为空 */
  findings: ReviewFinding[];
}

/** 每份文档的最终产出。 */
interface DocOutcome {
  doc: string;
  ok: boolean;
  approved: boolean;
  summary: CaseSummary | null;
  error: string;
}

interface Finding {
  /** 源文档路径 */
  where: string;
  /** 一句话：发现了什么 */
  what: string;
  /** 依据：产出文件与评审情况 */
  evidence: string;
  /** verified=独立评审通过；unconfirmed=未通过或未评审 */
  status: "verified" | "unconfirmed";
  /** medium=评审两轮后仍有意见；其余 low */
  severity: "low" | "medium" | "high";
}

interface WorkflowReport {
  /** 两三句话回答用户要的结果 */
  conclusion: string;
  findings: Finding[];
  /** 本次运行检查了什么、怎么查的 */
  verified: string[];
  /** 没有覆盖什么、为什么 */
  notCovered: string[];
}

// —— 盘点输入文档（md / pdf / docx / xlsx / xls），排除工作流自身的产物 ——
const patterns = ["**/*.md", "**/*.pdf", "**/*.docx", "**/*.xlsx", "**/*.xls"];
let docs: string[] = [];
for (const pat of patterns) {
  docs.push(...(await files.glob(pat)));
}
docs = docs
  .filter((p) => !p.startsWith("out/") && !p.startsWith(".zcode/") && !p.includes("node_modules/"))
  .sort();
const DOC_CAP = 10;
const truncated = docs.length > DOC_CAP;
const targets = docs.slice(0, DOC_CAP);
log(`待处理文档 ${targets.length} 份：${targets.join("、")}`);

const flatName = (p: string) => p.replace(/\.[^.]+$/, "").replace(/[\\/]+/g, "-");
const baseName = (p: string) => p.split(/[\\/]+/).pop() ?? p;

/** 按格式给子代理的原文提取方法（均已在本机验证可用）。 */
const extractionHint = (p: string): string => {
  if (p.endsWith(".md")) {
    return `Markdown 文件，直接用 Read 工具读取 ${p} 即可。`;
  }
  if (p.endsWith(".docx")) {
    return `docx 文件（${p}）。建议写临时 .py 脚本执行（python-docx 已安装），参考：\nimport docx\nd = docx.Document(r"${p}")\nfor para in d.paragraphs: print(para.text)\nfor t in d.tables:\n    for row in t.rows: print(" | ".join(c.text for c in row.cells))`;
  }
  if (p.endsWith(".pdf")) {
    return `pdf 文件（${p}）。建议写临时 .py 脚本执行（pypdf 已安装），参考：\nfrom pypdf import PdfReader\nr = PdfReader(r"${p}")\nfor page in r.pages: print(page.extract_text())`;
  }
  return `Excel 文件（${p}）。建议写临时 .py 脚本执行（openpyxl 已安装），参考：\nimport openpyxl\nwb = openpyxl.load_workbook(r"${p}", data_only=True)\nfor ws in wb.worksheets:\n    print("## sheet:", ws.title)\n    for row in ws.iter_rows(values_only=True): print(" | ".join(str(c) if c is not None else "" for c in row))`;
};

artifact.table("docs", {
  title: "各文档用例产出",
  key: "doc",
  columns: [
    { field: "doc", label: "文档" },
    { field: "module", label: "模块" },
    { field: "cases", label: "用例数" },
    { field: "status", label: "状态" },
  ],
});

phase("逐个文档撰写并评审测试用例");
// 每份文档独立链路：撰写 → 独立评审 →（最多两轮）修订，互不等待，最后一次性汇合。
const outcomes: DocOutcome[] = (
  await Promise.all(
    targets.map(async (p, i): Promise<DocOutcome> => {
      const base = baseName(p);
      const flat = flatName(p);
      const outFile = `out/testcases/${flat}.json`;
      const author = agent(`撰写·${i + 1}-${base}`, {
        system:
          "你是资深测试设计工程师，擅长从需求文档（md/pdf/docx/excel）提炼可测功能点并设计测试用例：" +
          "覆盖正向、边界、异常与规则组合，步骤可执行、预期可判定。只基于文档内容，不臆造不存在的功能；" +
          "文档信息不足时在 notes 里说明假设。若文档不可读或要求无法满足，如实说明并升级，不要伪造结果。",
      });
      const reviewer = agent(`评审·${i + 1}-${base}`, {
        system:
          "你是独立的测试用例评审员：对照原始需求逐条复查用例集，只找问题、不夸奖、不修改任何文件。" +
          "意见要具体（缺哪条规则、哪个预期错了、哪步不可执行），让撰写员能直接修改。" +
          "若原文档本身无法读取或用例文件缺失，如实说明并升级，不要凭空下结论。",
      });
      try {
        let summary = await author.ask<CaseSummary>(
          `请基于需求文档生成测试用例。

源文档：${p}
${extractionHint(p)}

要求：
1. 先完整读取源文档，提炼所有可测的功能点/业务规则（包括表格与附注里的规则、组合与互斥关系、上限、超时）。
2. 为每个功能点设计用例，覆盖：正向场景、边界值、异常/错误场景、规则组合。
3. 用例必须可执行：步骤具体、预期结果可判定。优先级：P0=核心功能正向；P1=重要功能与典型异常；P2=边界与次要场景。
4. 把结果写入文件 ${outFile}（UTF-8 JSON），结构：
{"document": "${p}", "module": "业务模块名", "cases": [{"id": "TC-01", "title": "用例标题", "priority": "P0", "type": "功能测试", "preconditions": "前置条件", "steps": ["1. ...", "2. ..."], "expected": "预期结果", "source": "来源章节或规则ID，如 §2 / R03"}]}
id 在本文档内唯一，从 TC-01 递增。
5. 不要修改源文档；除 ${outFile} 和临时提取脚本外不要新建文件。
若文档完全不含可测需求，返回 caseCount=0 并在 notes 说明，不要编造用例。`,
        );
        if (summary.caseCount === 0) {
          log(`「${base}」无可测需求，跳过评审`);
          report({ doc: p, module: summary.module, cases: 0, status: "无可测需求，跳过" }, "docs");
          return { doc: p, ok: true, approved: true, summary, error: "" };
        }
        let approved = false;
        for (let round = 1; round <= 2 && !approved; round++) {
          const review = await reviewer.ask<ReviewResult>(
            `第 ${round} 轮评审。请独立复查一份测试用例集：只凭原文档与用例文件判断，只找问题。

原始需求文档：${p}
${extractionHint(p)}
用例集文件：${outFile}

逐项检查：
1. 覆盖：原文档每个功能点/规则（含组合、互斥、上限、超时）是否都有对应用例？列出遗漏。
2. 正确性：用例的预期结果是否与原文档一致？
3. 可执行性：步骤是否具体可操作、预期是否可判定？
4. 结构：P0/P1/P2 是否得当？文件是否符合约定 JSON 结构？
可直接交付时 approved=true 且 findings 为空；每条意见一句话写清缺什么/错在哪。`,
          );
          approved = review.approved || review.findings.every((f) => f.severity === "low");
          if (approved) break;
          log(`「${base}」第 ${round} 轮评审提出 ${review.findings.length} 条意见，交回撰写员修订`);
          summary = await author.ask<CaseSummary>(
            `评审员对 ${outFile} 提出以下意见：
${JSON.stringify(review.findings, null, 2)}

请逐条处理：补充遗漏的用例、修正预期结果与步骤、调整优先级，直接更新文件 ${outFile}（保持同一 JSON 结构），然后返回更新后的摘要。low 级意见酌情处理。`,
          );
        }
        report(
          {
            doc: p,
            module: summary.module,
            cases: summary.caseCount,
            status: approved ? "已通过评审" : "两轮修订后仍有意见",
          },
          "docs",
        );
        return { doc: p, ok: true, approved, summary, error: "" };
      } catch (e) {
        report({ doc: p, module: "-", cases: 0, status: "处理失败" }, "docs");
        return { doc: p, ok: false, approved: false, summary: null, error: String(e) };
      }
    }),
  )
).filter((o): o is DocOutcome => o !== null);

const okOutcomes = outcomes.filter((o) => o.ok && o.summary !== null && o.summary.caseCount > 0);
const totalCases = okOutcomes.reduce((n, o) => n + (o.summary === null ? 0 : o.summary.caseCount), 0);
log(`文档处理完成：${okOutcomes.length} 份成功，共 ${totalCases} 条用例`);

let renderOk = false;
let gateError = "";
if (okOutcomes.length > 0) {
  phase("渲染并校验五种输出格式");
  const renderer = agent("渲染工程师", {
    system:
      "你是确定性工具工程师：写可重复运行、无网络依赖的 Python 脚本（本机 Python 3.12，已装 xlwt/xlrd/yaml/openpyxl/python-docx/pypdf）。" +
      "脚本失败时打印明确原因并以非零退出码结束；无法满足要求就如实说明并升级，不要伪造成功。",
  });
  await renderer.ask(
    `请编写 tools/render_all.py：把 out/testcases/ 下所有用例 JSON 渲染为 5 种格式的汇总文件，并在最后回读校验。工作目录为仓库根，运行方式：python tools/render_all.py。

输入：out/testcases/*.json（共 ${okOutcomes.length} 份，先读其中一份确认实际字段）。约定结构：
{"document": "...", "module": "...", "cases": [{"id": "TC-01", "title": "...", "priority": "P0|P1|P2", "type": "...", "preconditions": "...", "steps": ["..."], "expected": "...", "source": "..."}]}
合并时按 document 字段排序；对缺失字段做防御性兜底（空字符串/空数组），不要因个别字段整体失败。

输出（覆盖写）：
1. out/TestCases.xls — 用 xlwt：每个文档一个 sheet（sheet 名用模块名，替换 : / ? * 等非法字符、超 31 字符截断）；表头「用例ID/模块/标题/优先级/类型/前置条件/步骤/预期结果/来源」，步骤多行合并为一个单元格（内部换行）；设置列宽（标题约 40、前置条件约 30、步骤约 60、预期结果约 40），表头加粗。
2. out/TestCases.yaml — yaml.safe_dump({"documents": [各文档对象]}, allow_unicode=True, sort_keys=False)。
3. out/TestCases.json — 同一结构，ensure_ascii=False, indent=2。
4. out/TestCases.md — 一级标题「测试用例汇总」；每个文档二级标题（模块名 + 文档名）；用例表格列与 xls 相同，单元格内步骤用 <br> 连接。
5. out/TestCases.xmind — 标准zip（ZIP_DEFLATED），含三个条目：content.json、manifest.json（{"file-entries": {}}）、metadata.json（{"creator": {"name": "glm-test-workflow", "version": "1"}}）。content.json 是 JSON 数组，参考结构（已验证）：
[{"id": "sheet-1", "class": "sheet", "title": "测试用例", "rootTopic": {"id": "root", "class": "topic", "title": "测试用例汇总", "children": {"attached": [{"id": "d1", "class": "topic", "title": "模块名", "children": {"attached": [{"id": "c1", "class": "topic", "title": "TC-01 P0 用例标题", "children": {"attached": [{"id": "s1", "class": "topic", "title": "预期：..."}]}}]}}]}}}}]
每个文档一层（标题=模块名），其下每条用例一个 topic（标题=「id 优先级 标题」），用例的子 topic：前置条件、每一步、预期结果；所有 id 唯一（计数器即可）。

内置校验（渲染完成后自动执行，任一失败把原因打印到 stderr 并以退出码 1 结束）：
- xls：用 xlrd 重新打开，sheet 数=文档数，且每个 sheet 行数=对应用例数+1；
- yaml 与 json：解析回来，用例总数一致；
- md：非空且包含每个模块名；
- xmind：zipfile 打开，json 解析 content.json，rootTopic 下文档层 topic 数=文档数。
全部通过时逐行打印各文件校验结果（如「xls OK 4 sheets / 32 cases」）并以退出码 0 结束。
脚本内不访问网络。写完后你可自行运行一次冒烟测试并修复明显问题；工作流随后会正式运行它作为验收。`,
  );

  for (let round = 1; round <= 3; round++) {
    const check = await world.run("python", ["tools/render_all.py"], { timeoutMs: 300000 });
    if (check.exitCode === 0) {
      renderOk = true;
      log("五种格式渲染并回读校验通过：" + check.stdout.trim().split("\n").slice(-3).join(" / "));
      break;
    }
    gateError = (check.stderr.trim() || check.stdout.trim()).slice(0, 3000);
    log(`渲染校验第 ${round} 次未通过，交回渲染工程师修复`);
    if (round < 3) {
      await renderer.ask(
        `运行 python tools/render_all.py 失败（退出码 ${check.exitCode}）：
${gateError}

请修复 tools/render_all.py（保持输出与校验要求不变）。`,
      );
    }
  }

  const missing: string[] = [];
  try {
    await artifact.file("excel", "out/TestCases.xls", { title: "测试用例汇总（Excel .xls）", primary: true });
  } catch {
    missing.push("out/TestCases.xls");
  }
  try {
    await artifact.file("yaml", "out/TestCases.yaml", { title: "测试用例（YAML）" });
  } catch {
    missing.push("out/TestCases.yaml");
  }
  try {
    await artifact.file("json", "out/TestCases.json", { title: "测试用例（JSON）" });
  } catch {
    missing.push("out/TestCases.json");
  }
  try {
    await artifact.file("markdown", "out/TestCases.md", { title: "测试用例（Markdown）" });
  } catch {
    missing.push("out/TestCases.md");
  }
  try {
    await artifact.file("xmind", "out/TestCases.xmind", { title: "测试用例思维导图（XMind）" });
  } catch {
    missing.push("out/TestCases.xmind");
  }
  if (missing.length > 0) {
    log(`未能发布：${missing.join("、")}`);
  }
}

const findings: Finding[] = outcomes.map((o): Finding => {
  if (!o.ok || o.summary === null) {
    return {
      where: o.doc,
      what: "处理失败，未产出用例",
      evidence: o.error,
      status: "unconfirmed",
      severity: "high",
    };
  }
  const s = o.summary;
  if (s.caseCount === 0) {
    return {
      where: o.doc,
      what: "文档无可测需求，已跳过",
      evidence: "撰写员判定无需求：" + s.notes,
      status: "unconfirmed",
      severity: "low",
    };
  }
  return {
    where: o.doc,
    what: `产出 ${s.caseCount} 条用例（P0 ${s.p0} / P1 ${s.p1} / P2 ${s.p2}），模块「${s.module}」${s.notes ? "；备注：" + s.notes : ""}`,
    evidence:
      `用例文件 out/testcases/${flatName(o.doc)}.json；` +
      (o.approved ? "独立评审通过" : "两轮修订后评审仍有未决意见"),
    status: o.approved ? "verified" : "unconfirmed",
    severity: o.approved ? "low" : "medium",
  };
});

const conclusion =
  targets.length === 0
    ? "工作区中没有找到 md/pdf/docx/xlsx 文档，未生成测试用例。"
    : okOutcomes.length === 0
      ? `找到 ${targets.length} 份文档，但没有一份成功产出测试用例，详见 findings。`
      : renderOk
        ? `已从 ${okOutcomes.length} 份文档（md/docx/pdf/xlsx）生成 ${totalCases} 条测试用例，输出 .xls / .yaml / .json / .md / .xmind 五种格式，全部通过回读校验，产物在 out/TestCases.*。`
        : `从 ${okOutcomes.length} 份文档生成 ${totalCases} 条用例，但渲染脚本经 3 轮修复仍未通过校验，五种格式输出可能不完整（详见 notCovered）；各文档的用例 JSON 在 out/testcases/ 下仍可直接使用。`;

const verified: string[] = [];
if (okOutcomes.length > 0) {
  verified.push("每份文档的用例集由未参与撰写的评审员对照原文档独立复查（最多两轮修订）");
  if (renderOk) {
    verified.push(
      "python tools/render_all.py 渲染后对 5 个产物逐一回读校验（xls 用 xlrd 重开、yaml/json 解析、md 内容检查、xmind 压缩包与 content.json 解析），退出码 0",
    );
  }
}

const notCovered: string[] = [];
if (okOutcomes.length > 0) {
  notCovered.push("out/TestCases.xmind 仅做压缩包与 JSON 结构校验，未在 XMind 客户端中人工打开确认显示效果");
  notCovered.push("samples/ 下的 docx/pdf/xlsx 为演示管线而造的样例需求，业务复杂度低于真实文档");
}
if (truncated) {
  notCovered.push(`共发现 ${docs.length} 份文档，超过单次上限 ${DOC_CAP}，其余 ${docs.length - DOC_CAP} 份本次未处理`);
}
if (okOutcomes.length > 0 && !renderOk) {
  notCovered.push("渲染校验未通过：" + gateError.slice(0, 300));
}

const report2: WorkflowReport = { conclusion, findings, verified, notCovered };
return report2;
