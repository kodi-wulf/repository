import React from "https://esm.sh/react@19";
import { createRoot } from "https://esm.sh/react-dom@19/client";

const files = Array.isArray(window.KODIWULF_FILES) ? window.KODIWULF_FILES : [];
const roots = Array.isArray(window.KODIWULF_TREE?.roots) ? window.KODIWULF_TREE.roots : [];
const packageFiles = files.filter((file) => file.category !== "installer");
const h = React.createElement;

const delay = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function directFiles(path) {
  const prefix = `${path}/`;
  return packageFiles
    .filter((file) => file.path.startsWith(prefix))
    .sort((left, right) => left.name.localeCompare(right.name, "de"));
}

function pluginSections() {
  return [...new Set(packageFiles
    .filter((file) => file.path.startsWith("plugins/"))
    .map((file) => file.path.split("/")[1])
    .filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "de"));
}

function ZipIcon() {
  return h("span", { className: "zip-icon", "aria-hidden": "true" },
    h("svg", { viewBox: "0 0 48 48", focusable: "false" },
      h("path", { d: "M8 5h21l11 11v27H8z" }),
      h("path", { className: "zip-fold", d: "M29 5v12h11" }),
      h("path", { className: "zip-teeth", d: "M18 7h7v5h-7zm0 7h7v5h-7zm0 7h7v5h-7zm0 7h7v5h-7z" }),
      h("rect", { className: "zip-grip", x: "16", y: "34", width: "11", height: "6", rx: "2" })
    ));
}

function FolderIcon() {
  return h("span", { className: "folder-icon", "aria-hidden": "true" },
    h("svg", { viewBox: "0 0 52 42", focusable: "false" },
      h("path", { d: "M3 8h18l5 6h23v24H3z" }),
      h("path", { d: "M3 8V4h17l5 6h24v5" })
    ));
}

function Hamburger({ open }) {
  return h("span", { className: `hamburger${open ? " is-open" : ""}`, "aria-hidden": "true" },
    h("i"), h("i"), h("i"));
}

function FileRows({ path, query }) {
  const normalized = query.trim().toLowerCase();
  const list = directFiles(path).filter((file) => !normalized || file.name.toLowerCase().includes(normalized));

  React.useEffect(() => {
    window.anime?.({
      targets: ".zip-row",
      opacity: [0, 1],
      translateY: [16, 0],
      delay: window.anime.stagger(24),
      duration: 420,
      easing: "easeOutCubic"
    });
    if (window.d3 && list.length) {
      const maximum = Math.max(...list.map((file) => Number(file.size) || 0), 1);
      const scale = window.d3.scaleSqrt().domain([0, maximum]).range([8, 100]);
      window.d3.selectAll(".size-meter span").style("width", (datum, index, nodes) => `${scale(Number(nodes[index].dataset.size) || 0)}%`);
    }
  }, [path, query]);

  if (!list.length) return h("div", { className: "empty" }, "Keine passenden ZIP-Dateien gefunden.");
  return h("ul", { className: "zip-list" }, list.map((file) =>
    h("li", { className: "zip-row", key: file.path },
      h(ZipIcon),
      h("div", { className: "zip-copy" },
        h("a", { className: "zip-name", href: file.url, download: "" }, file.name),
        h("span", { className: "zip-path" }, file.path),
        h("span", { className: "size-meter", "aria-hidden": "true" }, h("span", { "data-size": file.size || 0 }))
      ),
      h("div", { className: "zip-facts" }, h("strong", null, file.category === "defekt" ? "Defekt · nur Download" : "ZIP"), h("span", null, file.size_label || "–")),
      h("a", { className: "download-button", href: file.url, download: "", "aria-label": `${file.name} herunterladen` }, "↓")
    )
  ));
}

function RepositoryBrowser() {
  const [path, setPath] = React.useState("");
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const sections = pluginSections();

  const navigate = (next) => {
    setQuery("");
    setPath(next);
    if (next.startsWith("plugins/")) setDrawerOpen(false);
  };

  React.useEffect(() => {
    document.documentElement.classList.add("js-ready");
    window.anime?.({ targets: ".folder-card", opacity: [0, 1], scale: [.96, 1], delay: window.anime.stagger(70), duration: 380, easing: "easeOutQuad" });
  }, [path]);

  React.useEffect(() => {
    if (!drawerOpen) return;
    window.anime?.({ targets: ".plugin-drawer", opacity: [0, 1], height: [0, "auto"], duration: 420, easing: "easeOutCubic" });
    window.anime?.({ targets: ".drawer-link", opacity: [0, 1], translateX: [-18, 0], delay: window.anime.stagger(70, { start: 100 }), duration: 320, easing: "easeOutQuad" });
  }, [drawerOpen]);

  const root = path === "";
  const title = root ? "ZIP-Kategorien" : path === "defekt" ? "Defekt · nur Download" : `${path}/`;

  return h(React.Fragment, null,
    h("div", { className: "browser-toolbar" },
      h("div", { className: "breadcrumb-line" },
        h("button", { className: "crumb", onClick: () => navigate("") }, "root"),
        path && h("span", { className: "crumb-separator" }, "/"),
        path && h("button", { className: "crumb current", onClick: () => {} }, path)
      ),
      h("strong", { className: "browser-title" }, title)
    ),
    root ? h("div", { className: "root-view" },
      h("div", { className: "folder-grid" },
        h("button", { className: `folder-card${drawerOpen ? " active" : ""}`, onClick: () => setDrawerOpen((open) => !open), "aria-expanded": drawerOpen, "aria-controls": "plugin-drawer" },
          h(FolderIcon), h("span", { className: "folder-copy" }, h("strong", null, "plugins/"), h("small", null, `${sections.length} Unterordner`)), h(Hamburger, { open: drawerOpen })),
        roots.filter((folder) => folder.name !== "plugins").map((folder) =>
          h("button", { className: "folder-card", key: folder.name, onClick: () => navigate(folder.name) },
            h(FolderIcon), h("span", { className: "folder-copy" }, h("strong", null, `${folder.name}/`),
              h("small", null, `${folder.zip_count} ZIP-Dateien${folder.download_only ? " · nur Download" : ""}`)),
            h("span", { className: "folder-arrow" }, "→")))
      ),
      drawerOpen && h("nav", { id: "plugin-drawer", className: "plugin-drawer", "aria-label": "Plugin-Unterordner" },
        h("div", { className: "drawer-head" }, h(Hamburger, { open: true }), h("span", null, "plugins/")),
        sections.map((section) => h("button", { className: "drawer-link", key: section, onClick: () => navigate(`plugins/${section}`) },
          h(FolderIcon), h("span", null, h("strong", null, `${section}/`), h("small", null, `${directFiles(`plugins/${section}`).length} ZIP-Dateien`)), h("b", null, "→")))
      )
    ) : h("div", { className: "files-view" },
      h("div", { className: "file-actions" },
        h("button", { className: "back-button", onClick: () => path.startsWith("plugins/") ? (setPath(""), setDrawerOpen(true)) : navigate("") }, "← zurück"),
        h("label", { className: "zip-search" }, h("span", null, "ZIP suchen"), h("input", { type: "search", value: query, onChange: (event) => setQuery(event.target.value), placeholder: "Dateiname …" }))
      ),
      h(FileRows, { path, query })
    )
  );
}

async function typeTerminal() {
  const solid = document.querySelector("[data-terminal-solid]");
  const shadow = document.querySelector("[data-terminal-shadow]");
  if (!solid || !shadow) return;
  const phrases = [
    "repository.kodi-wulf",
    "plugin.video.youtube",
    "program add-ons",
    "direct ZIP downloads",
    "Kodi repository updates"
  ];
  let phraseIndex = 0;
  while (true) {
    const phrase = phrases[phraseIndex % phrases.length];
    for (let index = 1; index <= phrase.length; index += 1) {
      solid.textContent = shadow.textContent = phrase.slice(0, index);
      await delay(58 + Math.random() * 48);
    }
    await delay(1250);
    for (let index = phrase.length - 1; index >= 0; index -= 1) {
      solid.textContent = shadow.textContent = phrase.slice(0, index);
      await delay(28 + Math.random() * 25);
    }
    await delay(340);
    phraseIndex += 1;
  }
}

function boot() {
  const summary = document.getElementById("react-summary");
  if (summary) createRoot(summary).render(h("span", null, `${packageFiles.length} ZIP-Dateien online`));
  const browser = document.getElementById("react-browser");
  if (browser) createRoot(browser).render(h(RepositoryBrowser));

  if (window.Vue && document.getElementById("vue-runtime")) {
    window.Vue.createApp({ data: () => ({ ready: "Vue · Jekyll · React bereit" }), template: "<span>{{ ready }}</span>" }).mount("#vue-runtime");
  }

  window.jQuery?.(() => {
    window.jQuery(".hero-banner").on("mousemove", function (event) {
      const bounds = this.getBoundingClientRect();
      const x = ((event.clientX - bounds.left) / bounds.width - .5) * 6;
      const y = ((event.clientY - bounds.top) / bounds.height - .5) * 3;
      this.style.setProperty("--parallax-x", `${x}px`);
      this.style.setProperty("--parallax-y", `${y}px`);
    }).on("mouseleave", function () {
      this.style.setProperty("--parallax-x", "0px");
      this.style.setProperty("--parallax-y", "0px");
    });
  });

  window.anime?.({ targets: ".animate-target", opacity: [0, 1], translateY: [24, 0], delay: window.anime.stagger(120), duration: 760, easing: "easeOutCubic" });
  const fontsReady = document.fonts?.ready ?? Promise.resolve();
  fontsReady.then(typeTerminal);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
