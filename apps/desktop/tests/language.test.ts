import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_LANGUAGE, LANGUAGE_STORAGE_KEY, getLanguage, initializeLanguage,
  normalizeLanguage, readLanguagePreference, setLanguage, subscribeLanguage,
} from "../src/i18n/language.ts";
import { translate } from "../src/i18n/translate.ts";
import { english } from "../src/i18n/en.ts";
import { helpTopicForSubject, searchHelpTopics } from "../src/utils/helpContent.ts";

function installDom(blocked = false) {
  const storage = new Map<string, string>();
  const events = new Map<string, (event: { key: string | null; newValue: string | null }) => void>();
  const root = { lang: "" };
  Object.assign(globalThis, {
    window: {
      localStorage: {
        getItem: (key: string) => { if (blocked) throw Error("blocked"); return storage.get(key) ?? null; },
        setItem: (key: string, value: string) => { if (blocked) throw Error("blocked"); storage.set(key, value); },
      },
      addEventListener: (name: string, handler: (event: { key: string | null; newValue: string | null }) => void) => events.set(name, handler),
      removeEventListener: (name: string) => events.delete(name),
    },
    document: { documentElement: root },
  });
  return { storage, events, root };
}

test("Language preference survives restart and defaults safely without storage", () => {
  const dom = installDom();
  initializeLanguage();
  assert.equal(getLanguage(), DEFAULT_LANGUAGE);
  setLanguage("en-US");
  assert.equal(dom.storage.get(LANGUAGE_STORAGE_KEY), "en-US");
  assert.equal(dom.root.lang, "en-US");
  initializeLanguage();
  assert.equal(getLanguage(), "en-US");
  assert.equal(normalizeLanguage(" EN-us "), "en-US");
  assert.equal(normalizeLanguage("invalid"), "zh-CN");
  const blocked = installDom(true);
  assert.equal(readLanguagePreference(), "zh-CN");
  setLanguage("en-US");
  assert.equal(blocked.root.lang, "en-US");
  delete (globalThis as Record<string, unknown>).window;
  delete (globalThis as Record<string, unknown>).document;
  initializeLanguage();
  assert.equal(getLanguage(), "zh-CN");
});

test("Subscribers update immediately and follow cross-window preference changes", () => {
  const dom = installDom();
  initializeLanguage();
  let notifications = 0;
  const unsubscribe = subscribeLanguage(() => notifications++);
  setLanguage("en-US");
  assert.equal(notifications, 1);
  dom.events.get("storage")!({ key: "unrelated", newValue: "zh-CN" });
  assert.equal(getLanguage(), "en-US");
  dom.events.get("storage")!({ key: LANGUAGE_STORAGE_KEY, newValue: "zh-CN" });
  assert.equal(getLanguage(), "zh-CN");
  assert.equal(dom.root.lang, "zh-CN");
  dom.storage.set(LANGUAGE_STORAGE_KEY, "en-US");
  dom.events.get("storage")!({ key: null, newValue: null });
  assert.equal(getLanguage(), "en-US");
  unsubscribe();
  assert.equal(dom.events.has("storage"), false);
});

test("Display translation preserves Chinese mode, whitespace, and non-text values", () => {
  const source = "  受体\n";
  assert.equal(translate(source, undefined, "zh-CN"), source);
  assert.equal(translate(source, undefined, "en-US"), "  Receptor\n");
  const object = { project_name: "受体", file: "研究/配体.pdbqt" };
  assert.equal(translate(object, undefined, "en-US"), object);
  assert.equal(translate(42, undefined, "en-US"), 42);
  assert.equal(translate("未收录的后端详情", undefined, "en-US"), "未收录的后端详情");
});

test("The restored v1.0.4 toolbar and navigation headings translate without changing Chinese labels", () => {
  for (const [source, target] of Object.entries({
    "项目工作台": "Project workspace",
    "导航": "Navigation",
    "工作台": "Workbench",
    "支持": "Support",
  })) {
    assert.equal(translate(source, undefined, "en-US"), target);
    assert.equal(translate(source, undefined, "zh-CN"), source);
  }
});

test("Unmapped text cannot resolve inherited object properties as translations", () => {
  for (const value of ["constructor", "toString", "__proto__", "hasOwnProperty", "valueOf"]) {
    assert.equal(translate(value, undefined, "en-US"), value);
    assert.equal(translate(value, undefined, "zh-CN"), value);
    assert.equal(translate(` ${value}\n`, undefined, "en-US"), ` ${value}\n`);
  }
});

test("Whitespace-prefixed templates resolve both explicit and computed messages", () => {
  const prefixed = Object.entries(english).filter(([source]) => /^\s/.test(source));
  assert.ok(prefixed.length >= 6);
  for (const [source, target] of prefixed) {
    const values = source.includes("输出") ? ["runs/中文 目录/out.pdbqt"] : ["2", "3"];
    const expand = (text: string) => text.replace(/\{(\d+)\}/g, (_, index) => values[Number(index)]);
    const computed = expand(source);
    const expected = (source.match(/^\s*/)?.[0] ?? "") + expand(target).trim();
    assert.equal(translate(source, values, "en-US"), expected, source);
    assert.equal(translate(computed, undefined, "en-US"), expected, computed);
    assert.equal(translate(computed, undefined, "zh-CN"), computed);
  }
});

test("Templates preserve user names, paths, spaces, and literal placeholder characters", () => {
  const source = "{0} 项目目录已保留在 {1}。可使用页头“打开已有项目”重新选择该目录。";
  const values = [" 项目：受体 {1} $& ", "D:\\研究  数据\\中文项目\\配体.pdbqt"];
  const result = translate(source, values, "en-US");
  for (const value of values) assert.ok(result.includes(value));
  const computed = `测试 项目目录已保留在 ${values[1]}。可使用页头“打开已有项目”重新选择该目录。`;
  const translated = translate(computed, undefined, "en-US");
  assert.ok(translated.includes(values[1]));
  assert.ok(translated.includes("测试"));
  assert.ok(translated.includes("project"));
});

test("All English messages preserve placeholders and the scientific disclaimer", () => {
  const slots = (text: string) => (text.match(/\{\d+\}/g) ?? []).sort();
  for (const [source, target] of Object.entries(english)) {
    assert.deepEqual(slots(target), slots(source), source);
    assert.ok(target.trim(), source);
    assert.doesNotMatch(target, /[\u3400-\u9fff]/, source);
  }
  assert.match(translate("Docking score 仅供结构结合趋势参考，不能替代实验验证。", undefined, "en-US"), /cannot replace experimental validation/i);
});

test("Combined structure warnings translate every sentence and fixed review subjects", () => {
  const receptor = "当前文件未包含足够化学信息；需要原始 PDB/mmCIF 才能检查残基记录连续性。；当前文件未包含足够化学信息；需要原始 PDB/mmCIF 才能检查水分子。";
  const ligand = "未读到明确立体标记；这不等于分子不存在立体异构，需要人工核对。；DockStart 未枚举或判定互变异构体；当前结构按来源文件原样进入准备流程。";
  for (const message of [receptor, ligand]) {
    assert.equal(translate(message, undefined, "zh-CN"), message);
    assert.doesNotMatch(translate(message, undefined, "en-US"), /[\u3400-\u9fff]/);
  }
});

test("Offline help can be searched in both languages and resolves English subjects", () => {
  assert.ok(searchHelpTopics("受体").some(topic => topic.id === "structure"));
  assert.ok(searchHelpTopics("receptor").some(topic => topic.id === "structure"));
  assert.equal(helpTopicForSubject("Binding energy")?.id, "results");
  assert.equal(helpTopicForSubject("Pose scoring")?.id, "tasks");
  assert.equal(helpTopicForSubject("Exhaustiveness")?.id, "parameters");
});
