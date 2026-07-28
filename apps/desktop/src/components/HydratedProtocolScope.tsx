import { Drop, Flask, GridFour, LockSimple, Target } from "@phosphor-icons/react";

type HydratedProtocolScopeProps = {
  compact?: boolean;
};

const scopeItems = [
  { icon: Flask, label: "单配体" },
  { icon: LockSimple, label: "刚性受体" },
  { icon: Target, label: "全局对接" },
  { icon: GridFour, label: "AD4 maps" },
];

export default function HydratedProtocolScope({
  compact = false,
}: HydratedProtocolScopeProps) {
  return (
    <section
      aria-label="水合 AD4 协议适用范围"
      className={`hydrated-scope ${compact ? "compact" : ""}`.trim()}
    >
      <div className="hydrated-scope-heading">
        <Drop aria-hidden="true" size={18} weight="duotone" />
        <div>
          <strong>Experimental</strong>
          {!compact ? <span>实验性水合 AD4 协议</span> : null}
        </div>
      </div>
      <div className="hydrated-scope-tags">
        {scopeItems.map(({ icon: Icon, label }) => (
          <span key={label}>
            <Icon aria-hidden="true" size={15} />
            {label}
          </span>
        ))}
      </div>
      {!compact ? (
        <div className="hydrated-scope-boundary">
          <p>不用于虚拟筛选，也不支持跨配体直接比较分值。</p>
          <strong>处理后评分未计算。</strong>
        </div>
      ) : null}
    </section>
  );
}
