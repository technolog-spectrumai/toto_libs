(function () {
  const LANGUAGE_ALIASES = {
    py: "python",
    python3: "python",
    tex: "latex",
    latex: "latex",
    svg: "svg",
    md: "markdown",
    markdown: "markdown",
    json: "json",
    sh: "bash",
    shell: "bash",
    bash: "bash",
    text: "text",
    txt: "text",
  };

  const registry = new Map();
  let activeZoneId = null;
  const aiBlocks = [];

  function normalizeLanguage(language) {
    if (!language) return "text";
    return LANGUAGE_ALIASES[String(language).trim().toLowerCase()] || "text";
  }

  function getZones() {
    return Array.from(registry.values());
  }

  function getZone(zoneId) {
    return registry.get(zoneId);
  }

  function emit(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function registerZone(zone) {
    if (!zone || !zone.id) return;
    const normalized = {
      ...zone,
      language: normalizeLanguage(zone.language),
      acceptedLanguages: (zone.acceptedLanguages || [zone.language || "text"]).map(normalizeLanguage),
    };
    registry.set(normalized.id, normalized);
    emit("functional-zone:registered", { zone: normalized, zones: getZones() });
    updateZoneChrome();
  }

  function unregisterZone(zoneId) {
    registry.delete(zoneId);
    if (activeZoneId === zoneId) activeZoneId = null;
    emit("functional-zone:unregistered", { zoneId, zones: getZones() });
    updateZoneChrome();
  }

  function getActiveZone() {
    return activeZoneId ? registry.get(activeZoneId) : undefined;
  }

  function setActiveZone(zoneId) {
    if (!registry.has(zoneId)) return;
    activeZoneId = zoneId;
    emit("functional-zone:active-changed", { zone: getActiveZone(), zones: getZones() });
    updateZoneChrome();
  }

  function getCompatibleZones(language) {
    const normalized = normalizeLanguage(language);
    return getZones().filter(zone => zone.acceptedLanguages.includes(normalized));
  }

  function getContent(zoneId) {
    const zone = getZone(zoneId);
    return zone && typeof zone.getContent === "function" ? zone.getContent() : "";
  }

  function applyToZone(zoneId, content, mode) {
    const zone = getZone(zoneId);
    if (!zone) return false;

    const action = mode || "insert";
    let result;
    try {
      if (action === "replace-selection" && typeof zone.replaceSelection === "function") {
        result = zone.replaceSelection(content);
      } else if (action === "set" && typeof zone.setContent === "function") {
        result = zone.setContent(content);
      } else if (action === "append" && typeof zone.appendContent === "function") {
        result = zone.appendContent(content);
      } else if (typeof zone.insertContent === "function") {
        result = zone.insertContent(content);
      } else if (typeof zone.appendContent === "function") {
        result = zone.appendContent(content);
      } else if (typeof zone.setContent === "function") {
        result = zone.setContent(content);
      } else {
        return false;
      }
    } catch (error) {
      console.warn("Functional zone import failed:", error);
      emit("functional-zone:content-apply-failed", { zone, mode: action, error });
      alert(`Could not import Steven output into ${zone.label}. ${error.message || "The content was not accepted by this target."}`);
      return false;
    }

    if (result === false) {
      emit("functional-zone:content-apply-failed", { zone, mode: action });
      return false;
    }

    emit("functional-zone:content-applied", { zone, mode: action });
    return true;
  }

  function useRegisterFunctionalZone(zoneDefinition, dependencies) {
    const zone = typeof zoneDefinition === "function" ? zoneDefinition() : zoneDefinition;
    registerZone(zone);
    const target = zone.element || (zone.elementId ? document.getElementById(zone.elementId) : null);
    const activate = () => setActiveZone(zone.id);
    target?.addEventListener("focusin", activate);
    target?.addEventListener("click", activate);

    return {
      update(nextZoneDefinition) {
        const nextZone = typeof nextZoneDefinition === "function" ? nextZoneDefinition() : nextZoneDefinition;
        registerZone({ ...zone, ...nextZone, id: zone.id });
      },
      unregister() {
        target?.removeEventListener("focusin", activate);
        target?.removeEventListener("click", activate);
        unregisterZone(zone.id);
      },
    };
  }

  function parseAIOutputBlocks(text, sourceMessageId) {
    const blocks = [];
    const fencePattern = /```([a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)```/g;
    let match;

    while ((match = fencePattern.exec(text)) !== null) {
      const language = normalizeLanguage(match[1] || "text");
      blocks.push({
        id: `${sourceMessageId}-block-${blocks.length + 1}`,
        sourceMessageId,
        type: language === "latex" ? "latex" : language === "svg" ? "svg" : language === "markdown" ? "markdown" : language === "text" ? "text" : "code",
        language,
        content: match[2].trim(),
        createdAt: new Date().toISOString(),
      });
    }

    if (!blocks.length && text.trim()) {
      blocks.push({
        id: `${sourceMessageId}-block-1`,
        sourceMessageId,
        type: "text",
        language: "text",
        content: text.trim(),
        createdAt: new Date().toISOString(),
      });
    }

    return blocks;
  }

  function rememberAIBlocks(blocks) {
    blocks.forEach(block => {
      const existingIndex = aiBlocks.findIndex(existing => existing.id === block.id);
      if (existingIndex >= 0) aiBlocks.splice(existingIndex, 1, block);
      else aiBlocks.unshift(block);
    });
    emit("functional-zone:ai-blocks-changed", { blocks: aiBlocks.slice() });
  }

  function getAIBlocks(language) {
    const normalized = language ? normalizeLanguage(language) : null;
    return aiBlocks.filter(block => !normalized || block.language === normalized || block.language === "text");
  }

  function buttonClass() {
    const base = "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-semibold shadow-sm transition hover:opacity-85";
    const isDark = localStorage.getItem("darkMode") === "true";
    return isDark
      ? `${base} border-accent-1 bg-bubble-bg-dark text-text-main-dark`
      : `${base} border-accent-2 bg-bubble-bg-light text-text-main-light`;
  }

  function createActionButton(label, iconClass, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = buttonClass();
    button.innerHTML = `<i class="${iconClass}"></i><span></span>`;
    button.querySelector("span").textContent = label;
    button.addEventListener("click", onClick);
    return button;
  }

  function importLatestBlockIntoZone(zoneId) {
    const zone = getZone(zoneId);
    if (!zone) return;
    setActiveZone(zoneId);
    const blocks = getAIBlocks().filter(block => zone.acceptedLanguages.includes(block.language));
    if (!blocks.length) {
      alert(`No Steven output is compatible with ${zone.label} yet.`);
      return;
    }
    const block = blocks[0];
    const preferredMode = zone.metadata?.preferredImportMode || "insert";
    applyToZone(zoneId, block.content, preferredMode);
  }

  function previewLatestBlockForZone(zoneId) {
    const zone = getZone(zoneId);
    if (!zone) return;
    setActiveZone(zoneId);
    const block = getAIBlocks().find(candidate => zone.acceptedLanguages.includes(candidate.language));
    if (!block) {
      alert(`No Steven output is compatible with ${zone.label} yet.`);
      return;
    }

    const previewWindow = window.open("", "_blank", "noopener,noreferrer");
    if (!previewWindow) return;
    const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    }[char]));
    previewWindow.document.write(`
      <title>Preview import into ${escapeHtml(zone.label)}</title>
      <style>
        body { margin: 0; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: #f3f4f6; color: #0f172a; }
        header { padding: 12px 16px; background: #0f172a; color: white; font-weight: 700; }
        main { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px; }
        section { border: 1px solid #cbd5e1; border-radius: 8px; background: white; overflow: hidden; }
        h2 { margin: 0; padding: 8px 10px; border-bottom: 1px solid #e2e8f0; font-size: 12px; }
        pre { margin: 0; padding: 10px; white-space: pre-wrap; overflow: auto; max-height: calc(100vh - 98px); }
      </style>
      <header>Preview import into ${escapeHtml(zone.label)}</header>
      <main>
        <section><h2>Current</h2><pre>${escapeHtml(getContent(zoneId))}</pre></section>
        <section><h2>New content</h2><pre>${escapeHtml(block.content)}</pre></section>
      </main>
    `);
    previewWindow.document.close();
  }

  function renderZoneToolbar(container, zoneId, options) {
    const zone = getZone(zoneId);
    if (!zone || !container) return;
    container.innerHTML = "";
    if (options?.extraButtons) options.extraButtons.forEach(button => container.appendChild(button));
  }

  function updateZoneChrome() {
    document.querySelectorAll("[data-functional-zone-id]").forEach(element => {
      const isActive = element.dataset.functionalZoneId === activeZoneId;
      element.classList.toggle("ring-2", isActive);
      element.classList.toggle("ring-accent-light", isActive);
      element.querySelectorAll("[data-active-target-badge]").forEach(badge => {
        badge.classList.toggle("hidden", !isActive);
      });
    });
  }

  function initDeclarativeZones() {
    document.querySelectorAll("[data-functional-zone-id]").forEach(element => {
      const zoneId = element.dataset.functionalZoneId;
      element.addEventListener("focusin", () => setActiveZone(zoneId));
      element.addEventListener("click", () => setActiveZone(zoneId));
    });
  }

  document.addEventListener("DOMContentLoaded", initDeclarativeZones);

  window.FunctionalZones = {
    registerZone,
    unregisterZone,
    getZone,
    getZones,
    getActiveZone,
    setActiveZone,
    getCompatibleZones,
    getContent,
    applyToZone,
    useRegisterFunctionalZone,
    parseAIOutputBlocks,
    rememberAIBlocks,
    getAIBlocks,
    renderZoneToolbar,
    importLatestBlockIntoZone,
    previewLatestBlockForZone,
  };
})();
