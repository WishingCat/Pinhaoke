(async () => {
  const RECEIVER_URL = "__RECEIVER_URL__";
  const RECEIVER_TOKEN = "__RECEIVER_TOKEN__";
  const PKU_ORIGIN = "https://elective.pku.edu.cn";
  const TERM = "26-27学年第1学期";
  const QUERY_PATH = "/elective2008/edu/pku/stu/elective/controller/courseQuery/getCurriculmByForm.do";
  const PAGER_PATH = "/elective2008/edu/pku/stu/elective/controller/courseQuery/queryCurriculum.jsp";
  const DETAIL_PATH = "/elective2008/edu/pku/stu/elective/controller/courseQuery/goNested.do";
  const detailBySeq = new Map();

  const BASIC_HEADERS = [
    "课程号", "课程名", "课程类别", "学分", "教师", "班号", "开课单位",
    "专业", "年级", "上课时间及教室", "限数已选", "备注",
  ];
  const DETAIL_FIELDS = [
    "英文名称", "周学时", "总学时", "开课学期", "修读对象",
    "参考书", "课程简介", "详情备注", "大纲",
  ];
  const DETAIL_ALIASES = {
    "英文名称": ["英文名称", "英文名", "课程英文名称", "课程名称"],
    "周学时": ["周学时"],
    "总学时": ["总学时"],
    "开课学期": ["开课学期"],
    "修读对象": ["修读对象"],
    "参考书": ["参考书", "参考书目"],
    "课程简介": ["课程简介", "简介"],
    "详情备注": ["详情备注", "备注"],
    "大纲": ["大纲", "教学大纲"],
  };
  const UNIQUE_KEY_FIELDS = ["课程号", "班号", "教师", "开课单位"];

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function cleanText(text) {
    return String(text || "")
      .replace(/\uFFFC/g, "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t\r\f\v]+/g, " ")
      .replace(/ *\n[ \t]*/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function textOf(node) {
    if (!node) return "";
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const tag = node.tagName.toLowerCase();
    if (tag === "br") return "\n";
    let out = "";
    for (const child of node.childNodes) out += textOf(child);
    if (["p", "div", "li", "tr", "table", "tbody"].includes(tag)) out += "\n";
    return out;
  }

  function cellText(cell) {
    return cleanText(textOf(cell)).replace(/\n+/g, "");
  }

  function normLabel(label) {
    return cleanText(label).replace(/[：: ]+$/g, "");
  }

  function extractEnglishName(value) {
    const text = cleanText(value);
    const parts = text.split(/\s+-\s+/);
    if (parts.length >= 2) return cleanText(parts.slice(1).join(" - "));
    return /[A-Za-z]/.test(text) && !/[\u4e00-\u9fff]/.test(text) ? text : "";
  }

  async function postProgress(payload) {
    try {
      await fetch(`${RECEIVER_URL}/progress`, {
        method: "POST",
        mode: "cors",
        headers: {
          "Content-Type": "application/json",
          "X-PKU-Receiver-Token": RECEIVER_TOKEN,
        },
        body: JSON.stringify(payload),
      });
    } catch (_) {
      // Progress is best effort; final data is posted separately.
    }
  }

  function assertExpectedTerm() {
    if (location.origin !== PKU_ORIGIN || location.pathname !== QUERY_PATH) {
      throw new Error(`wrong PKU query page: ${location.href}`);
    }
    const pageText = document.body ? document.body.innerText : "";
    if (!pageText.includes(TERM)) throw new Error(`expected term not visible: ${TERM}`);
  }

  function setStatus(message) {
    document.title = message.slice(0, 80);
    let box = document.getElementById("pku-graduate-scrape-status");
    if (!box) {
      box = document.createElement("div");
      box.id = "pku-graduate-scrape-status";
      box.style.cssText = [
        "position:fixed", "right:12px", "bottom:12px", "z-index:2147483647",
        "max-width:560px", "padding:10px 12px", "background:#102a43",
        "color:white", "font:13px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
        "border-radius:6px", "box-shadow:0 8px 24px rgba(0,0,0,.28)",
      ].join(";");
      document.body.appendChild(box);
    }
    box.textContent = message;
  }

  function formDataForAllGraduateCourses() {
    return new URLSearchParams({
      "{actionForm.courseID}": "",
      "{actionForm.courseName}": "",
      "wlw-select_key:{actionForm.deptID}OldValue": "true",
      "wlw-select_key:{actionForm.deptID}": "ALL",
      "wlw-select_key:{actionForm.courseDay}OldValue": "true",
      "wlw-select_key:{actionForm.courseDay}": "",
      "wlw-select_key:{actionForm.courseTime}OldValue": "true",
      "wlw-select_key:{actionForm.courseTime}": "",
      "wlw-checkbox_key:{actionForm.queryDateFlag}OldValue": "false",
      "deptIdHide": "",
    });
  }

  function assertAllowedPkuPath(path) {
    const url = new URL(path, PKU_ORIGIN);
    const allowedPaths = new Set([QUERY_PATH, PAGER_PATH, DETAIL_PATH]);
    if (url.origin !== PKU_ORIGIN || !allowedPaths.has(url.pathname)) {
      throw new Error(`disallowed PKU request: ${url.href}`);
    }
  }

  async function fetchText(path, options = {}, retries = 2) {
    assertAllowedPkuPath(path);
    const {
      headers: ignoredHeaders,
      signal: ignoredSignal,
      credentials: ignoredCredentials,
      ...requestOptions
    } = options;
    void ignoredHeaders;
    void ignoredSignal;
    void ignoredCredentials;
    const headers = {};
    if (requestOptions.method && requestOptions.method.toUpperCase() === "POST") {
      headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8";
    }

    let lastError;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch(path, {
          ...requestOptions,
          credentials: "include",
          headers,
          signal: controller.signal,
        });
        const html = await response.text();
        const probe = html.slice(0, 10000);
        if (probe.includes("提示:请不要用刷课机刷课") || probe.includes("<title>系统提示</title>")) {
          const error = new Error("PKU_SYSTEM_PROMPT");
          error.retryable = false;
          throw error;
        }
        if (!response.ok) {
          const error = new Error(`${path} HTTP ${response.status}`);
          error.retryable = response.status === 408 || response.status === 429 || response.status >= 500;
          throw error;
        }
        return html;
      } catch (error) {
        lastError = error;
        const retryable = error.retryable === true || error.name === "AbortError" || error instanceof TypeError;
        if (!retryable || attempt === retries) throw error;
      } finally {
        clearTimeout(timer);
      }
      await sleep(400 * (2 ** attempt));
    }
    throw lastError;
  }

  function pagerValues(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const options = Array.from(doc.querySelectorAll('select[name="netui_row"] option'));
    const values = options
      .map((option) => ({ value: option.value || option.textContent.trim(), label: cleanText(option.textContent) }))
      .filter((item) => item.value);
    if (values.length) return values;
    return [{ value: "syllabusListGrid;0", label: "1" }];
  }

  function pageUrl(pageValue) {
    const params = new URLSearchParams({ netui_row: pageValue });
    return `${PAGER_PATH}?${params.toString()}`;
  }

  function parseRows(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const table = doc.querySelector("table.datagrid");
    if (!table) return [];
    const rows = [];
    for (const tr of table.querySelectorAll("tr")) {
      if (tr.querySelector("th")) continue;
      const cells = Array.from(tr.children).filter((el) => el.tagName && el.tagName.toLowerCase() === "td");
      if (cells.length < BASIC_HEADERS.length) continue;
      const values = cells.map(cellText);
      const basic = {};
      BASIC_HEADERS.forEach((header, index) => {
        basic[header] = values[index] || "";
      });

      const detailLink = cells[0].querySelector('a[href*="goNested.do"][href*="course_seq_no="]');
      const detailUrl = detailLink ? new URL(detailLink.getAttribute("href"), location.href).href : "";
      const item = {
        "数据学期": TERM,
        "详情链接": detailUrl,
        "基本信息": basic,
        "详细信息": Object.fromEntries(DETAIL_FIELDS.map((field) => [field, ""])),
      };
      const seq = detailUrl.match(/course_seq_no=([^&]+)/);
      if (seq) item["课程序号"] = decodeURIComponent(seq[1]);
      rows.push(item);
    }
    return rows;
  }

  function parseDetail(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const details = Object.fromEntries(DETAIL_FIELDS.map((field) => [field, ""]));
    const aliasToField = {};
    for (const [field, aliases] of Object.entries(DETAIL_ALIASES)) {
      for (const alias of aliases) aliasToField[alias] = field;
    }

    function assign(label, value) {
      const rawLabel = normLabel(label);
      const field = aliasToField[rawLabel];
      if (!field || details[field]) return;
      if (field === "英文名称") {
        details[field] = extractEnglishName(value);
      } else {
        details[field] = cleanText(value);
      }
    }

    for (const tr of doc.querySelectorAll("tr")) {
      const cells = Array.from(tr.children)
        .filter((el) => ["th", "td"].includes(el.tagName.toLowerCase()))
        .map((el) => cleanText(textOf(el)));
      if (cells.length >= 2) {
        for (let i = 0; i < cells.length - 1; i += 2) assign(cells[i], cells[i + 1]);
      }
    }

    return details;
  }

  function validateDetailLink(item) {
    const seq = item["课程序号"];
    const link = item["详情链接"];
    if (typeof seq !== "string" || !seq.trim()) throw new Error("missing course sequence");
    if (typeof link !== "string" || !link.trim()) throw new Error(`missing detail link: ${seq}`);
    const url = new URL(link);
    const values = url.searchParams.getAll("course_seq_no");
    if (
      url.origin !== PKU_ORIGIN
      || url.protocol !== "https:"
      || url.host !== "elective.pku.edu.cn"
      || url.username
      || url.password
      || url.pathname !== DETAIL_PATH
      || url.hash
      || url.search.slice(1).split("&").length !== 1
      || !url.search.includes("=")
      || Array.from(url.searchParams.keys()).some((key) => key !== "course_seq_no")
      || values.length !== 1
      || values[0] !== seq
    ) {
      throw new Error(`invalid detail link: ${seq}`);
    }
    return url.href;
  }

  async function loadDetail(item) {
    const seq = item["课程序号"];
    const detailUrl = validateDetailLink(item);
    if (detailBySeq.has(seq)) return detailBySeq.get(seq);
    const pending = fetchText(detailUrl, { method: "GET" }).then(parseDetail);
    detailBySeq.set(seq, pending);
    try {
      const detail = await pending;
      detailBySeq.set(seq, detail);
      return detail;
    } catch (error) {
      if (detailBySeq.get(seq) === pending) detailBySeq.delete(seq);
      throw error;
    }
  }

  async function scrapeAllCourses() {
    setStatus("PKU graduate scrape: querying all courses");
    const firstHtml = await fetchText(QUERY_PATH, {
      method: "POST",
      body: formDataForAllGraduateCourses(),
    });
    const pages = pagerValues(firstHtml);
    let rows = parseRows(firstHtml);
    const pageStats = [{ page: 1, value: pages[0]?.value || "", rows: rows.length }];
    await postProgress({ stage: "list", page: 1, pages: pages.length, rows: rows.length });

    for (let index = 1; index < pages.length; index += 1) {
      const html = await fetchText(pageUrl(pages[index].value), { method: "GET" });
      const pageRows = parseRows(html);
      rows = rows.concat(pageRows);
      pageStats.push({ page: index + 1, value: pages[index].value, rows: pageRows.length });
      await postProgress({ stage: "list", page: index + 1, pages: pages.length, rows: pageRows.length });
      await sleep(120);
    }

    for (let index = 0; index < rows.length; index += 1) {
      const item = rows[index];
      item["详细信息"] = await loadDetail(item);
      if (index % 10 === 0 || index === rows.length - 1) {
        setStatus(`PKU graduate scrape: details ${index + 1}/${rows.length}`);
        await postProgress({ stage: "detail", done: index + 1, total: rows.length });
      }
      await sleep(90 + Math.floor(Math.random() * 90));
    }

    return { rows, pageStats };
  }

  function validatePayload(rows, pageStats, stats) {
    if (!Array.isArray(rows) || rows.length === 0) throw new Error("payload rows must be nonempty");
    const duplicateSeqs = [];
    const duplicateKeys = [];
    const missingDetailLinks = [];
    const missingCourseCodes = [];
    const seqSeen = new Set();
    const keySeen = new Set();

    const requiredBasic = new Set(BASIC_HEADERS);
    for (const [index, row] of rows.entries()) {
      if (!row || typeof row !== "object" || row["数据学期"] !== TERM) {
        throw new Error(`row ${index} has invalid term or shape`);
      }
      const bi = row["基本信息"] || {};
      const seq = row["课程序号"] || "";
      const basicKeys = Object.keys(bi);
      if (
        basicKeys.length !== BASIC_HEADERS.length
        || !Array.from(requiredBasic).every((key) => Object.hasOwn(bi, key))
        || !Object.values(bi).every((value) => typeof value === "string")
      ) {
        throw new Error(`row ${index} basic fields do not match schema`);
      }
      const detail = row["详细信息"];
      if (
        !detail
        || Object.keys(detail).length !== DETAIL_FIELDS.length
        || !DETAIL_FIELDS.every((field) => Object.hasOwn(detail, field))
        || !Object.values(detail).every((value) => typeof value === "string")
      ) {
        throw new Error(`row ${index} detail fields do not match schema`);
      }
      const key = UNIQUE_KEY_FIELDS.map((field) => bi[field] || "").join("\u0001");
      if (!bi["课程号"]) missingCourseCodes.push(key);
      if (!row["详情链接"]) missingDetailLinks.push(key);
      validateDetailLink(row);
      if (seqSeen.has(seq)) duplicateSeqs.push(seq);
      seqSeen.add(seq);
      if (keySeen.has(key)) duplicateKeys.push(key);
      keySeen.add(key);
    }

    if (
      !stats
      || Object.keys(stats).length !== 1
      || !Object.hasOwn(stats, "研究生课")
      || !Number.isInteger(stats["研究生课"])
      || stats["研究生课"] !== rows.length
    ) {
      throw new Error("stats do not match rows");
    }
    if (!Array.isArray(pageStats) || pageStats.length === 0) throw new Error("pageStats must be nonempty");
    const suspiciousPages = [];
    let pageTotal = 0;
    for (let i = 0; i < pageStats.length; i += 1) {
      const page = pageStats[i];
      if (!page || !Number.isInteger(page.rows) || page.rows < 0) throw new Error("invalid pageStats row count");
      pageTotal += page.rows;
      const isLast = i === pageStats.length - 1;
      if (!isLast && page.rows !== 100) {
        suspiciousPages.push({ page: page.page, rows: page.rows, expected: 100 });
      }
    }
    if (pageTotal !== rows.length) throw new Error("pageStats total does not match rows");
    const validation = {
      totalRows: rows.length,
      duplicateSeqs,
      duplicateKeys,
      missingDetailLinks,
      missingCourseCodes,
      suspiciousPages,
    };
    if (missingDetailLinks.length || missingCourseCodes.length || duplicateKeys.length || suspiciousPages.length) {
      throw new Error(`local payload validation failed: ${JSON.stringify(validation)}`);
    }
    return validation;
  }

  async function postDone(payload) {
    const response = await fetch(`${RECEIVER_URL}/done`, {
      method: "POST",
      mode: "cors",
      headers: {
        "Content-Type": "application/json",
        "X-PKU-Receiver-Token": RECEIVER_TOKEN,
      },
      body: JSON.stringify(payload),
    });
    const responseText = await response.text();
    if (!response.ok || responseText.trim() !== "ok") {
      throw new Error(`receiver HTTP ${response.status}: ${responseText.slice(0, 200)}`);
    }
  }

  try {
    assertExpectedTerm();
    setStatus("PKU graduate scrape: start");
    await postProgress({ stage: "start", url: location.href, term: TERM });
    const result = await scrapeAllCourses();
    const stats = { "研究生课": result.rows.length };
    const validation = validatePayload(result.rows, result.pageStats, stats);
    const payload = {
      scrapedAt: new Date().toISOString(),
      term: TERM,
      sourceUrl: location.href,
      stats,
      pageStats: result.pageStats,
      validation,
      errors: [],
      rows: result.rows,
    };
    setStatus(`PKU graduate scrape: posting ${result.rows.length} rows`);
    await postDone(payload);
    setStatus(`PKU graduate scrape: done ${result.rows.length} rows`);
  } catch (error) {
    const message = String(error && error.stack || error);
    setStatus(`PKU graduate scrape failed: ${message.slice(0, 300)}`);
    await postProgress({ stage: "fatal", message });
    throw error;
  }
})();
