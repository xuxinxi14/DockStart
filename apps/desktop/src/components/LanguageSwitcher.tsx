import { Translate } from "@phosphor-icons/react";
import { setLanguage } from "../i18n/language";
import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";

export default function LanguageSwitcher() {
  const language = useLanguage();
  return (
    <div className="language-switcher" role="group" aria-label={translate("界面语言")}>
      <Translate aria-hidden="true" size={15} />
      <button type="button" lang="zh-CN" aria-pressed={language === "zh-CN"}
        onClick={() => setLanguage("zh-CN")} title="切换为简体中文">
        中文
      </button>
      <button type="button" lang="en-US" aria-pressed={language === "en-US"}
        onClick={() => setLanguage("en-US")} title="Switch to English">
        EN
      </button>
    </div>
  );
}
