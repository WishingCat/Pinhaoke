(async () => {
  const RECEIVER_URL = "__RECEIVER_URL__";
  const TERM = "25-26学年第3学期";
  const QUERY_PATH = "/elective2008/edu/pku/stu/elective/controller/courseQuery/getCurriculmByForm.do";
  const PAGER_PATH = "/elective2008/edu/pku/stu/elective/controller/courseQuery/queryCurriculum.jsp";

  const COURSE_TYPES = [
    ["培养方案", "education_plan_bk"],
    ["专业课", "speciality"],
    ["政治课", "politics"],
    ["英语课", "english"],
    ["体育课", "gym"],
    ["通识课", "tsk_choice"],
    ["公选课", "pub_choice"],
    ["计算机基础课", "liberal_computer"],
    ["劳动教育课", "ldjyk"],
    ["思政选择性必修课", "szxzxbx"],
  ];
  const DEPT_ALL_TYPES = new Set(["speciality", "tsk_choice", "pub_choice"]);

  const BASIC_HEADERS = [
    "课程号", "课程名", "课程类别", "学分", "教师", "班号", "开课单位",
    "专业", "年级", "上课时间及教室", "限数已选", "自选PNP", "备注",
  ];
  const ENGLISH_HEADERS = [
    "课程号", "课程名", "英语等级", "课程类别", "学分", "教师", "班号", "开课单位",
    "专业", "年级", "上课时间及教室", "限数已选", "自选PNP", "备注",
  ];
  const DETAIL_FIELDS = [
    "英文名称", "先修课程", "中文简介", "英文简介", "成绩记载方式",
    "通识课所属系列", "授课语言", "教材", "参考书", "教学大纲", "教学评估",
  ];
  const DETAIL_ALIASES = {
    "英文名称": ["英文名称", "英文名", "课程英文名称"],
    "先修课程": ["先修课程", "先修要求"],
    "中文简介": ["中文简介", "课程中文简介", "课程简介(中文)", "课程简介（中文）"],
    "英文简介": ["英文简介", "课程英文简介", "课程简介(英文)", "课程简介（英文）"],
    "成绩记载方式": ["成绩记载方式", "成绩记录方式", "成绩方式"],
    "通识课所属系列": ["通识课所属系列", "通识课系列", "所属系列"],
    "授课语言": ["授课语言", "教学语言"],
    "教材": ["教材"],
    "参考书": ["参考书", "参考书目"],
    "教学大纲": ["教学大纲", "大纲"],
    "教学评估": ["教学评估", "课程评估", "评估"],
  };

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

  async function postProgress(payload) {
    try {
      await fetch(`${RECEIVER_URL}/progress`, {
        method: "POST",
        mode: "cors",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (_) {
      // Progress is best effort; final data is posted separately.
    }
  }

  function setStatus(message) {
    document.title = message.slice(0, 80);
    let box = document.getElementById("pku-summer-scrape-status");
    if (!box) {
      box = document.createElement("div");
      box.id = "pku-summer-scrape-status";
      box.style.cssText = [
        "position:fixed", "right:12px", "bottom:12px", "z-index:2147483647",
        "max-width:520px", "padding:10px 12px", "background:#102a43",
        "color:white", "font:13px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
        "border-radius:6px", "box-shadow:0 8px 24px rgba(0,0,0,.28)",
      ].join(";");
      document.body.appendChild(box);
    }
    box.textContent = message;
  }

  function formDataFor(typeValue) {
    const deptValue = DEPT_ALL_TYPES.has(typeValue) ? "ALL" : "";
    return new URLSearchParams({
      "wlw-radio_button_group_key:{actionForm.courseSettingType}": typeValue,
      "{actionForm.courseID}": "",
      "{actionForm.courseName}": "",
      "wlw-select_key:{actionForm.deptID}OldValue": "true",
      "wlw-select_key:{actionForm.deptID}": deptValue,
      "wlw-select_key:{actionForm.courseDay}OldValue": "true",
      "wlw-select_key:{actionForm.courseDay}": "",
      "wlw-select_key:{actionForm.courseTime}OldValue": "true",
      "wlw-select_key:{actionForm.courseTime}": "",
      "wlw-checkbox_key:{actionForm.queryDateFlag}OldValue": "false",
      "deptIdHide": "",
    });
  }

  async function fetchText(path, options = {}) {
    const response = await fetch(path, {
      credentials: "include",
      ...options,
      headers: {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        ...(options.headers || {}),
      },
    });
    const html = await response.text();
    if (!response.ok) throw new Error(`${path} HTTP ${response.status}`);
    const probe = html.slice(0, 10000);
    if (probe.includes("提示:请不要用刷课机刷课") || probe.includes("<title>系统提示</title>")) {
      throw new Error(`${path} returned system prompt`);
    }
    return html;
  }

  function pageCount(html) {
    const text = cleanText(new DOMParser().parseFromString(html, "text/html").body.textContent);
    const match = text.match(/Page\s+\d+\s+of\s+(\d+)/i);
    return match ? Number(match[1]) : 1;
  }

  function parseRows(html, typeName) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const table = doc.querySelector("table.datagrid");
    if (!table) return [];
    const rows = [];
    for (const tr of table.querySelectorAll("tr")) {
      if (tr.querySelector("th")) continue;
      const cells = Array.from(tr.children).filter((el) => el.tagName && el.tagName.toLowerCase() === "td");
      if (cells.length < 13) continue;
      const values = cells.map(cellText);
      const headers = typeName === "英语课" && values.length >= ENGLISH_HEADERS.length
        ? ENGLISH_HEADERS
        : BASIC_HEADERS;
      const basic = {};
      headers.forEach((header, index) => {
        basic[header] = values[index] || "";
      });

      const detailLink = cells[0].querySelector('a[href*="goNested.do"][href*="course_seq_no="]');
      const detailUrl = detailLink ? new URL(detailLink.getAttribute("href"), location.href).href : "";
      const item = {
        "课程类型": typeName,
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
      const field = aliasToField[normLabel(label)];
      if (field && !details[field]) details[field] = cleanText(value);
    }

    for (const tr of doc.querySelectorAll("tr")) {
      const cells = Array.from(tr.children)
        .filter((el) => ["th", "td"].includes(el.tagName.toLowerCase()))
        .map((el) => cleanText(textOf(el)));
      if (cells.length >= 2) {
        for (let i = 0; i < cells.length - 1; i += 2) assign(cells[i], cells[i + 1]);
      } else if (cells.length === 1) {
        assign(cells[0], "");
      }
    }

    const allText = cleanText(textOf(doc.body));
    for (const field of DETAIL_FIELDS) {
      if (details[field]) continue;
      for (const alias of DETAIL_ALIASES[field]) {
        const pattern = new RegExp(`${alias}[：:\\t ]+([\\s\\S]*?)(?=${DETAIL_FIELDS.join("|")}|$)`);
        const match = allText.match(pattern);
        if (match) {
          details[field] = cleanText(match[1]);
          break;
        }
      }
    }
    return details;
  }

  async function scrapeType(typeName, typeValue) {
    setStatus(`PKU summer scrape: querying ${typeName}`);
    const firstHtml = await fetchText(QUERY_PATH, {
      method: "POST",
      body: formDataFor(typeValue),
    });
    const pages = pageCount(firstHtml);
    let rows = parseRows(firstHtml, typeName);
    await postProgress({ stage: "list", type: typeName, page: 1, pages, rows: rows.length });

    for (let page = 2; page <= pages; page += 1) {
      const html = await fetchText(PAGER_PATH, {
        method: "POST",
        body: new URLSearchParams({ netui_row: String(page) }),
      });
      const pageRows = parseRows(html, typeName);
      rows = rows.concat(pageRows);
      await postProgress({ stage: "list", type: typeName, page, pages, rows: pageRows.length });
      await sleep(120);
    }

    for (let index = 0; index < rows.length; index += 1) {
      const item = rows[index];
      if (item["详情链接"]) {
        const html = await fetchText(item["详情链接"], { method: "GET", headers: {} });
        item["详细信息"] = parseDetail(html);
      }
      if (index % 10 === 0 || index === rows.length - 1) {
        setStatus(`PKU summer scrape: ${typeName} details ${index + 1}/${rows.length}`);
        await postProgress({ stage: "detail", type: typeName, done: index + 1, total: rows.length });
      }
      await sleep(90 + Math.floor(Math.random() * 90));
    }
    return rows;
  }

  async function postDone(payload) {
    const response = await fetch(`${RECEIVER_URL}/done`, {
      method: "POST",
      mode: "cors",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`receiver HTTP ${response.status}`);
  }

  try {
    const allRows = [];
    const stats = {};
    const errors = [];
    setStatus("PKU summer scrape: start");
    await postProgress({ stage: "start", url: location.href });

    for (const [typeName, typeValue] of COURSE_TYPES) {
      try {
        const rows = await scrapeType(typeName, typeValue);
        stats[typeName] = rows.length;
        allRows.push(...rows);
        await postProgress({ stage: "type_done", type: typeName, rows: rows.length });
      } catch (error) {
        errors.push({ type: typeName, message: String(error && error.message || error) });
        await postProgress({ stage: "type_error", type: typeName, message: String(error && error.message || error) });
      }
      await sleep(250);
    }

    const payload = {
      scrapedAt: new Date().toISOString(),
      term: TERM,
      stats,
      errors,
      rows: allRows,
    };
    setStatus(`PKU summer scrape: posting ${allRows.length} rows`);
    await postDone(payload);
    setStatus(`PKU summer scrape: done ${allRows.length} rows`);
  } catch (error) {
    const message = String(error && error.stack || error);
    setStatus(`PKU summer scrape failed: ${message.slice(0, 300)}`);
    await postProgress({ stage: "fatal", message });
    throw error;
  }
})();
