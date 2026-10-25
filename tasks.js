/* Preset jobs poll their own endpoint; they never request or reschedule quota reads. */
(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  const labels = {
    waiting: "等待中",
    running: "执行中",
    completed: "已完成",
    failed: "执行失败",
    cancelled: "已取消",
  };
  const defaults = ["gpt-6-astra", "gpt-6.1-sol"];
  const modelName = (model) =>
    ({ "gpt-6-astra": "GPT-6 Astra", "gpt-6.1-sol": "GPT-6.1 Sol" })[model] ||
    model;
  let settings = { directory: "", models: defaults },
    listBusy = false,
    signature = "",
    taskId = "",
    editing = false,
    detailId = "",
    detailCwd = "",
    detailText = { content: "", result: "" },
    saving = false;
  let selectedModels = new Set(),
    detectingModels = false;
  const stamp = (at) =>
    at
      ? new Intl.DateTimeFormat("sv-SE", {
          timeZone: "Asia/Shanghai",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }).format(new Date(at * 1000))
      : "—";
  const localTime = (at) => stamp(at).replace(" ", "T");

  async function request(route, body, timeout = 25000) {
    const controller = new AbortController(),
      timer = setTimeout(() => controller.abort(), timeout);
    try {
      const payload = JSON.stringify(body);
      const send = () =>
        fetch("/api/tasks/" + route, {
          method: "POST",
          signal: controller.signal,
          headers: {
            "Content-Type": "application/json",
            "X-Quota-Token": token,
          },
          body: payload,
        });
      let response = await send(),
        data = await response.json();
      if (response.status === 403 && data.code === "stale_session") {
        const fresh = await fetch("/api/session", {
          signal: controller.signal,
          headers: { "X-Quota-Session": "refresh" },
          cache: "no-store",
        });
        const session = await fresh.json();
        if (!fresh.ok || typeof session.token !== "string" || !session.token)
          throw Error("页面连接已失效，请刷新页面");
        token = session.token;
        response = await send();
        data = await response.json();
      }
      if (!response.ok) throw Error(data.error || "操作失败");
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };

  function renderTasks(data) {
    const tasks = data.tasks || [];
    settings = data.settings || settings;
    const waiting =
      data.counts?.waiting ??
      tasks.filter((t) => t.status === "waiting").length;
    const running =
      data.counts?.running ??
      tasks.filter((t) => t.status === "running").length;
    el("task-count").textContent = `${waiting} 个等待 · ${running} 个执行中`;
    const nextSignature = JSON.stringify(tasks);
    if (nextSignature === signature) return;
    signature = nextSignature;
    const rows = tasks.map((task) => {
      const row = make("div", undefined, "task-row"),
        info = make("div");
      info.append(make("div", task.title, "task-name"));
      info.title = task.path;
      const model = make(
        "div",
        `${modelName(task.model)} · ${task.effort}`,
        "task-model",
      );
      const plan = make(
        "div",
        task.mode === "quota" ? "五小时额度回满后" : stamp(task.runAt),
        "task-plan",
      );
      const state = make(
        "span",
        labels[task.status] || task.status,
        "task-status",
      );
      state.dataset.state = task.status;
      state.title = task.progress || state.textContent;
      state.setAttribute(
        "aria-label",
        state.textContent +
          (task.progress && task.progress !== state.textContent
            ? "：" + task.progress
            : ""),
      );
      const actions = make("div", undefined, "task-row-actions"),
        detail = make("button", "详情", "task-detail-button");
      detail.type = "button";
      detail.addEventListener("click", () => openDetail(task.id));
      if (task.status === "waiting" && !task.submittedAt) {
        const edit = make("button", "修改");
        edit.type = "button";
        edit.addEventListener("click", () => openEdit(task.id));
        actions.append(edit);
      }
      if (task.status === "waiting" || task.status === "running") {
        const cancel = make(
          "button",
          task.status === "running" ? "中断" : "取消",
        );
        cancel.type = "button";
        cancel.disabled = !!task.cancelRequested;
        cancel.addEventListener("click", async () => {
          cancel.disabled = true;
          try {
            await request("cancel", { id: task.id });
            await refreshTasks();
          } catch (error) {
            el("task-sync").textContent = error.message;
            cancel.disabled = false;
          }
        });
        actions.append(cancel);
      }
      actions.append(detail);
      const remove = make("button", undefined, "task-delete-button");
      remove.type = "button";
      remove.disabled = task.status === "running";
      remove.title = remove.disabled ? "运行中的任务请先中断" : "删除任务";
      remove.setAttribute("aria-label", "删除任务");
      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      icon.setAttribute("viewBox", "0 0 24 24");
      icon.setAttribute("aria-hidden", "true");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7");
      icon.append(path);
      remove.append(icon);
      remove.addEventListener("click", async () => {
        if (remove.disabled) return;
        remove.disabled = true;
        try {
          await request("delete", { id: task.id });
          if (detailId === task.id) {
            detailId = "";
            el("task-detail-dialog").close();
          }
          await refreshTasks();
        } catch (error) {
          el("task-sync").textContent = error.message;
          remove.disabled = false;
        }
      });
      actions.append(remove);
      row.append(info, model, plan, state, actions);
      return row;
    });
    if (!rows.length)
      rows.push(make("p", "尚无预设任务，点击“新建任务”添加。", "task-empty"));
    el("task-list").replaceChildren(...rows);
    trackScroll();
  }

  async function refreshTasks() {
    if (listBusy) return;
    listBusy = true;
    const controller = new AbortController(),
      timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/tasks", {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw Error("读取失败");
      renderTasks(await response.json());
      el("task-sync").textContent = "任务状态每 2 秒更新";
      if (detailId && el("task-detail-dialog").open) await refreshDetail();
    } catch (error) {
      el("task-sync").textContent =
        "任务状态暂未更新，正在重试；以上为上次记录。";
    } finally {
      clearTimeout(timer);
      listBusy = false;
    }
  }

  function updateModels(selected) {
    const select = el("task-model");
    select.replaceChildren();
    for (const model of new Set([...settings.models, ...(selected ? [selected] : [])])) {
      const option = make("option", modelName(model));
      option.value = model;
      select.append(option);
    }
    select.value = selected || defaults[0];
    el("task-default-hint").textContent =
      (settings.directory
        ? `默认文档目录：${settings.directory}。`
        : "尚未设置默认目录，请选择文档路径或先保存任务设置。") +
      " 执行目录须单独选择，Codex 只能修改该目录内的文件。暂停运行会阻止新任务开始。";
  }

  function sourceChanged() {
    const existing = el("task-source").value === "existing";
    el("task-content").readOnly = existing;
    el("task-content").required = !existing;
    el("task-read").hidden = !existing;
    el("task-path").required = existing;
    el("task-content").value = "";
    el("task-path").value = "";
  }

  el("task-new").addEventListener("click", () => {
    if (saving) return;
    editing = false;
    editControls();
    el("task-form").reset();
    taskId = crypto.randomUUID().replaceAll("-", "");
    el("task-time").value = localTime(Date.now() / 1000 + 3600);
    el("task-time-field").hidden = false;
    el("task-time").required = true;
    el("task-error").textContent = "";
    sourceChanged();
    updateModels(defaults[0]);
    el("task-dialog").showModal();
  });
  function editControls() {
    el("task-dialog-title").textContent = editing ? "修改预设任务" : "新建预设任务";
    el("task-save").textContent = editing ? "保存修改" : "添加任务";
    for (const id of ["task-source", "task-path", "task-pick-file"])
      el(id).disabled = editing;
  }
  async function openEdit(id) {
    if (saving) return;
    try {
      const { task } = await request("detail", { id });
      if (saving || el("task-dialog").open) return;
      if (task.status !== "waiting" || task.submittedAt)
        throw Error("任务已开始或结束，请刷新任务状态");
      el("task-form").reset();
      taskId = task.id;
      editing = true;
      editControls();
      sourceChanged();
      el("task-source").value = "snapshot";
      updateModels(task.model);
      for (const [field, value] of Object.entries({ title: task.title, model: task.model,
        effort: task.effort, mode: task.mode, time: task.runAt ? localTime(task.runAt) : "",
        path: task.path, cwd: task.cwd, content: task.content }))
        el("task-" + field).value = value;
      el("task-time-field").hidden = task.mode !== "time";
      el("task-time").required = task.mode === "time";
      el("task-default-hint").textContent = "修改保存在任务内容中，原 MD 文档保持原样。任务开始后不能修改。";
      el("task-error").textContent = "";
      el("task-dialog").showModal();
    } catch (error) {
      el("task-sync").textContent = error.message;
    }
  }
  el("task-mode").addEventListener("change", () => {
    const fixed = el("task-mode").value === "time";
    el("task-time-field").hidden = !fixed;
    el("task-time").required = fixed;
  });
  el("task-source").addEventListener("change", sourceChanged);

  async function readDocument() {
    el("task-error").textContent = "";
    const data = await request("read", { path: el("task-path").value });
    el("task-content").value = data.content;
  }
  el("task-read").addEventListener("click", async () => {
    try {
      await readDocument();
    } catch (error) {
      el("task-error").textContent = error.message;
    }
  });
  for (const [button, input, kind] of [
    ["task-pick-file", "task-path", null],
    ["task-pick-cwd", "task-cwd", "directory"],
    ["task-pick-directory", "task-directory", "directory"],
  ])
    el(button).addEventListener("click", async () => {
      const feedback =
        button === "task-pick-directory" ? "task-settings-error" : "task-error";
      el(button).disabled = true;
      el(feedback).textContent = "";
      try {
        const data = await request(
          "pick",
          {
            kind:
              kind ||
              (el("task-source").value === "existing" ? "open" : "save"),
          },
          190000,
        );
        if (data.path) {
          el(input).value = data.path;
          if (input === "task-path" && el("task-source").value === "existing")
            await readDocument();
        }
      } catch (error) {
        el(feedback).textContent = error.message;
      } finally {
        el(button).disabled = false;
      }
    });

  el("task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving) return;
    saving = true;
    el("task-save").disabled = true;
    el("task-error").textContent = "";
    try {
      const mode = el("task-mode").value;
      if (!el("task-cwd").value.trim()) throw Error("请选择执行目录，任务内容中的路径不会自动成为可写目录");
      const body = {
        id: taskId,
        title: el("task-title").value,
        mode,
        runAt:
          mode === "time"
            ? new Date(el("task-time").value + "+08:00").getTime() / 1000
            : null,
        model: el("task-model").value,
        effort: el("task-effort").value,
        source: el("task-source").value,
        path: el("task-path").value.trim(),
        cwd: el("task-cwd").value.trim(),
        content: el("task-content").value,
      };
      await request(editing ? "edit" : "create", body);
      el("task-dialog").close();
      await refreshTasks();
    } catch (error) {
      el("task-error").textContent =
        error.name === "AbortError"
          ? "结果未确认，请刷新任务状态后核对；重试不会重复创建任务。"
          : error.message;
    } finally {
      saving = false;
      el("task-save").disabled = false;
    }
  });

  async function detectModels(force = false) {
    if (detectingModels) return;
    detectingModels = true;
    el("task-models-refresh").disabled = true;
    el("task-model-detection").textContent = "正在检测本机 Codex 模型…";
    try {
      const data = await request("models", { force }, 45000);
      const options = (data.models || []).filter(
        (m) => !defaults.includes(m.model),
      );
      const tags = options.map((model) => {
        const label = make("label", undefined, "task-model-tag");
        const input = make("input");
        input.type = "checkbox";
        input.value = model.model;
        input.checked = selectedModels.has(model.model);
        input.addEventListener("change", () => {
          if (input.checked) selectedModels.add(model.model);
          else selectedModels.delete(model.model);
        });
        label.append(input, make("span", model.label || model.model));
        label.title = model.model;
        return label;
      });
      el("task-model-tags").replaceChildren(...tags);
      el("task-model-detection").textContent = options.length
        ? "已检测，可多选。"
        : "本机未返回其他模型。";
    } catch (error) {
      el("task-model-detection").textContent =
        "检测失败：" + error.message + "；可点击重新检测。";
    } finally {
      detectingModels = false;
      el("task-models-refresh").disabled = false;
    }
  }
  el("task-models-refresh").addEventListener("click", () => detectModels(true));
  el("task-settings-open").addEventListener("click", () => {
    el("task-directory").value = settings.directory;
    selectedModels = new Set(
      settings.models.filter((m) => !defaults.includes(m)),
    );
    el("task-model-tags").replaceChildren();
    el("task-settings-error").textContent = "";
    el("task-settings-dialog").showModal();
    detectModels();
  });
  el("task-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    el("task-settings-save").disabled = true;
    try {
      const models = [...selectedModels];
      await request("settings", {
        directory: el("task-directory").value.trim(),
        models,
      });
      settings = {
        directory: el("task-directory").value.trim(),
        models: [...new Set([...defaults, ...models])],
      };
      updateModels(el("task-model").value);
      el("task-settings-dialog").close();
      await refreshTasks();
    } catch (error) {
      el("task-settings-error").textContent = error.message;
    } finally {
      el("task-settings-save").disabled = false;
    }
  });

  function shortTaskPath(path, cwd) {
    if (typeof path !== "string" || typeof cwd !== "string" || !cwd) return path || "—";
    const windows = /^[a-z]:[\\/]|^\\\\/i.test(cwd);
    const normalized = path.replace(/[\\/]+/g, "/");
    const prefix = cwd.replace(/[\\/]+/g, "/").replace(/\/+$/, "") + "/";
    const within = windows
      ? normalized.toLowerCase().startsWith(prefix.toLowerCase())
      : normalized.startsWith(prefix);
    if (!within) return path;
    const separator = windows ? "\\" : "/";
    return "..." + separator + normalized.slice(prefix.length).replaceAll("/", separator);
  }

  function detailStatus(state, text = labels[state] || state) {
    const badge = el("task-detail-status");
    detailTextContent("task-detail-status", text);
    badge.dataset.state = state;
    badge.setAttribute("aria-label", "任务执行状态：" + text);
  }

  function detailProgress(text = "") {
    detailTextContent("task-detail-progress", text);
    el("task-detail-progress").hidden = !text;
  }

  function detailTextContent(id, text) {
    const node = el(id);
    text = String(text ?? "");
    if (node.textContent === text) return;
    // A polling refresh must not replace the text nodes backing a mouse selection.
    // Defer changed text until the selection is cleared; other details keep updating.
    const selection = document.getSelection?.();
    if (selection && !selection.isCollapsed && selection.rangeCount && selection.getRangeAt(0).intersectsNode(node)) return;
    node.textContent = text;
  }

  async function refreshDetail() {
    const id = detailId,
      data = await request("detail", { id });
    if (detailId !== id) return;
    const task = data.task;
    detailTextContent("task-detail-title", task.title);
    detailTextContent("task-detail-meta", `模型选择：${modelName(task.model)} · ${task.effort}`);
    detailCwd = task.cwd || "";
    el("task-detail-directory").hidden = !detailCwd;
    detailTextContent("task-detail-cwd", detailCwd);
    detailTextContent("task-detail-files",
      `任务文档：${shortTaskPath(task.path, task.cwd)}` +
      (task.resultPath ? `\n结果文档：${shortTaskPath(task.resultPath, task.cwd)}` : "") +
      (task.startedAt ? `\n开始时间：${stamp(task.startedAt)}` : "") +
      (task.finishedAt ? `\n结束时间：${stamp(task.finishedAt)}` : ""));
    el("task-detail-files").title = `任务文档：${task.path}` +
      (task.resultPath ? `\n结果文档：${task.resultPath}` : "");
    detailStatus(task.status);
    detailProgress(task.progress?.trim() === labels[task.status] ? "" : task.progress || "");
    detailText = { content: task.content || "", result: task.result || "" };
    detailTextContent("task-detail-content", detailText.content);
    detailTextContent("task-detail-result", detailText.result || (task.status === "waiting" ? "等待执行" : "暂无回复"));
    for (const key of ["content", "result"]) el("task-copy-" + key).disabled = !detailText[key];
  }
  async function openDetail(id) {
    detailId = id;
    detailCwd = "";
    detailText = { content: "", result: "" };
    for (const key of ["content", "result"]) {
      el("task-copy-" + key).disabled = true;
      el("task-copy-" + key + "-feedback").textContent = "";
    }
    el("task-detail-directory").hidden = true;
    el("task-detail-files").textContent = "";
    el("task-detail-files").title = "";
    el("task-path-feedback").textContent = "";
    el("task-copy-tooltip").textContent = "复制路径";
    el("task-detail-title").textContent = "任务详情";
    el("task-detail-meta").textContent = "";
    el("task-detail-meta").title = "";
    el("task-detail-content").textContent = "";
    el("task-detail-result").textContent = "";
    detailStatus("loading", "读取中");
    detailProgress();
    el("task-detail-dialog").showModal();
    try {
      await refreshDetail();
    } catch (error) {
      detailStatus("failed", "读取失败");
      detailProgress(error.message);
    }
  }

  for (const key of ["content", "result"]) {
    el("task-copy-" + key).addEventListener("click", async () => {
      if (!detailText[key]) return;
      const id = detailId;
      const feedback = el("task-copy-" + key + "-feedback");
      try {
        await navigator.clipboard.writeText(detailText[key]);
        if (detailId === id) feedback.textContent = "已复制";
      } catch (error) {
        if (detailId === id) feedback.textContent = "复制失败：" + error.message;
      }
    });
  }

  el("task-copy-cwd").addEventListener("click", async () => {
    if (!detailCwd) return;
    const id = detailId;
    try {
      await navigator.clipboard.writeText(detailCwd);
      if (detailId === id) {
        el("task-path-feedback").textContent = "";
        el("task-copy-tooltip").textContent = "已复制路径";
      }
    } catch (error) {
      if (detailId === id) el("task-path-feedback").textContent = "复制失败：" + error.message;
    }
  });
  el("task-open-cwd").addEventListener("click", async () => {
    if (!detailCwd || el("task-open-cwd").disabled) return;
    const id = detailId;
    el("task-open-cwd").disabled = true;
    try {
      await request("open-directory", { id });
      if (detailId === id) el("task-path-feedback").textContent = "";
    } catch (error) {
      if (detailId === id) el("task-path-feedback").textContent = error.message;
    } finally {
      el("task-open-cwd").disabled = false;
    }
  });

  for (const button of document.querySelectorAll("[data-close]"))
    button.addEventListener("click", () => el(button.dataset.close).close());
  for (const dialog of document.querySelectorAll(".task-dialog"))
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const r = dialog.getBoundingClientRect();
      if (
        event.clientX < r.left ||
        event.clientX > r.right ||
        event.clientY < r.top ||
        event.clientY > r.bottom
      )
        dialog.close();
    });

  const targets = [
    "schedule-panel",
    "quota-chart-panel",
    "tasks-panel",
    "activity-panel",
    "settings-panel",
  ];
  const gap = () =>
    parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--card-gap"),
    ) || 20;
  function jumpTo(id) {
    const maximum = Math.max(
      0,
      document.documentElement.scrollHeight - window.innerHeight,
    );
    const requested =
      id === "schedule-panel"
        ? 0
        : el(id).getBoundingClientRect().top + window.scrollY - gap();
    window.scrollTo({
      top: Math.max(0, Math.min(requested, maximum)),
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  }
  const nodes = [...document.querySelectorAll(".page-nodes button")];
  nodes.forEach((button) =>
    button.addEventListener("click", () => jumpTo(button.dataset.target)),
  );
  for (const [id, target] of [
    ["quota-chart-jump", "quota-chart-panel"],
    ["tasks-jump", "tasks-panel"],
    ["activity-jump", "activity-panel"],
    ["settings-jump", "settings-panel"],
  ])
    el(id).addEventListener("click", () => jumpTo(target));

  function trackScroll() {
    document.body.classList.toggle("page-scrolled", window.scrollY > 0);
    const tops = targets.map(
      (id) => el(id).getBoundingClientRect().top + window.scrollY - gap(),
    );
    let index = 0;
    tops.forEach((top, i) => {
      if (window.scrollY >= top - 2) index = i;
    });
    if (
      window.scrollY > 0 &&
      window.scrollY + window.innerHeight >=
        document.documentElement.scrollHeight - 2
    )
      index = targets.length - 1;
    const fraction =
      index < tops.length - 1
        ? Math.max(
            0,
            Math.min(
              1,
              (window.scrollY - tops[index]) / (tops[index + 1] - tops[index]),
            ),
          )
        : 0;
    nodes.forEach((node, i) => {
      if (i === index) node.setAttribute("aria-current", "location");
      else node.removeAttribute("aria-current");
      node.dataset.passed = String(i < index);
    });
    el("node-progress").style.height =
      ((index + fraction) / (tops.length - 1)) * 100 + "%";
  }
  window.addEventListener("scroll", trackScroll, { passive: true });
  window.addEventListener("resize", trackScroll);
  window.addEventListener("load", trackScroll);
  trackScroll();
  refreshTasks();
  setInterval(refreshTasks, 2000);
})();
