const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class Node {
  constructor(tag, value = "") {
    this.tag = tag;
    this.value = value;
    this.children = [];
    this.style = {};
  }
  appendChild(child) {
    if (child.tag === "#fragment") this.children.push(...child.children);
    else this.children.push(child);
    return child;
  }
  replaceChildren(...children) {
    this.children = [];
    children.forEach(child => this.appendChild(child));
  }
  set textContent(value) {
    this.value = String(value);
    this.children = [];
  }
  get textContent() {
    return this.value + this.children.map(child => child.textContent).join("");
  }
}

const document = {
  createElement: tag => new Node(tag),
  createTextNode: text => new Node("#text", text),
  createDocumentFragment: () => new Node("#fragment"),
};
const context = { window: {}, document };
const source = fs.readFileSync(path.join(__dirname, "../static/js/assistant-chat.js"), "utf8");
vm.runInNewContext(source, context);
const render = context.window.WarehouseAssistant.renderAnswer;

function descendants(node, tag) {
  return [...(node.tag === tag ? [node] : []), ...node.children.flatMap(child => descendants(child, tag))];
}

test("助手回复把表格与加粗渲染为 DOM，同时保留原始文本安全", () => {
  const host = new Node("div");
  render(host, [
    "找到 **3 条**物料",
    "",
    "| 物料编码 | 规格 | 库存 |",
    "|---|:---|---:|",
    "| 10103901010060 | 7号电池 | **22** |",
    "| 10200301010780 | HIP-FL0101V2.0 | 4 |",
    "",
    "<img src=x onerror=alert(1)>",
  ].join("\n"));
  const table = descendants(host, "table")[0];
  assert.ok(table);
  assert.equal(descendants(table, "th").length, 3);
  assert.equal(descendants(table, "td").length, 6);
  assert.equal(descendants(table, "td")[2].style.textAlign, "right");
  assert.equal(descendants(table, "strong")[0].textContent, "22");
  assert.equal(descendants(host, "img").length, 0);
  assert.match(host.textContent, /<img src=x onerror=alert\(1\)>/);
});

test("表格转义竖线及代码块保持内容", () => {
  const host = new Node("div");
  render(host, "| 规格 | 数量 |\n|---|---|\n| A\\|B | 2 |\n\n```\n| A | B |\n```");
  assert.equal(descendants(host, "table").length, 1);
  assert.equal(descendants(host, "td")[0].textContent, "A|B");
  assert.equal(descendants(host, "pre")[0].textContent, "| A | B |");
});
