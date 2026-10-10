import { useId } from "react";
import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import { normalizeReceptorTransparency } from "./receptorTransparency";

type Props = {
  value: number;
  defaultValue: number;
  onChange: (value: number) => void;
  disabled?: boolean;
};

export default function ReceptorTransparencyControl({ value, defaultValue, onChange, disabled = false }: Props) {
  useLanguage();
  const id = useId();
  const percentage = normalizeReceptorTransparency(value);
  return (
    <div className="receptor-transparency-control">
      <label htmlFor={id} title={translate("0% 为不透明，100% 为完全隐藏；只改变受体显示，不改变配体和结构坐标。")}>{translate("受体透明度")}</label>
      <output htmlFor={id}>{percentage}%</output>
      <input
        id={id}
        type="range"
        min={0}
        max={100}
        step={1}
        value={percentage}
        disabled={disabled}
        aria-valuetext={translate("{0}% 透明", [percentage])}
        onChange={event => onChange(normalizeReceptorTransparency(Number(event.target.value)))}
      />
      <button type="button" disabled={disabled} onClick={() => onChange(defaultValue)} aria-label={translate("重置受体透明度")}>{translate("重置")}</button>
    </div>
  );
}
